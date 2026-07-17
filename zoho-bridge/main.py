# Chatwoot webhook receiver. Handlers (one per event type):
#
#   1. message_created (first incoming)         → spam pipeline + classify
#                                                 + team-route + Option-D
#                                                 Zoho escalation
#   2. conversation_status_changed (→ "open")   → manual bot handoff →
#                                                 Zoho ticket
#   3. conversation_updated                     → priority flagged high /
#                                                 urgent → Zoho ticket with
#                                                 SLA dueDate
#   4. message_created (outgoing on reviews)    → post agent reply back to
#                                                 Google Business Profile
#
# Each handler is self-contained; add more by writing a function and
# wiring it in the dispatcher at the bottom.

import asyncio
import hashlib
import hmac
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from langfuse import get_client

import bms
import config
import chatwoot
import classifier
import retail_showrooms as retail
import document_extractor
import summarizer
import tracing
import zoho
import zoho_crm
import google_reviews as gr
import review_reply
import reviews_poller
import reviews_state
import crm_state

crm_state.init()  # round-robin counters for govt/bulk owner rotation

app = FastAPI()


def _now_iso() -> str:
    """UTC timestamp in ISO 8601, suitable for logging and persisting on
    Chatwoot custom_attributes."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.on_event("startup")
async def _start_reviews_poller():
    # Boot-safe: run_forever() no-ops if Google isn't configured yet.
    asyncio.create_task(reviews_poller.run_forever())


@app.on_event("shutdown")
async def _flush_langfuse():
    # Langfuse batches trace exports in the background; flush on shutdown so
    # in-flight generations aren't lost when the worker exits.
    get_client().flush()


# ── Option-D Zoho-Desk escalation decision ────────────────────────────────
# Decides whether an auto-classified, team-routed message ALSO creates a
# Zoho Desk ticket. Three signals (in priority order):
#
#   1. UNIVERSAL HIGH-PRIORITY OVERRIDE — any conversation flagged high/urgent
#      always escalates, regardless of team or content.
#   2. LEGAL TEAM ALWAYS — anything classified to the legal team escalates
#      (compliance / audit trail).
#   3. CLASSIFIER ESCALATION_SIGNAL — the AI classifier returns a structured
#      escalation_signal field (legal_or_compliance / hr_sensitive /
#      financial_dispute / brand_or_contract / none) and an audit-trail
#      escalation_reason. This REPLACED a hand-maintained per-team keyword
#      regex list after review feedback ("manual regex matching? can we use
#      AI?"). Since we already pay for the classifier call, adding two JSON
#      fields to that response was free and far more accurate than keyword
#      matching — see classifier.EMAIL_TYPE_SYSTEM_PROMPT for the rubric.
#   4. MANUAL HANDOFF — bot transitions pending → open. Handled separately
#      in handle_status_changed() (always escalates).

# Map classifier escalation_signal → human-friendly reason suffix on the
# private-note label. Keeps the routing logic decoupled from the prose.
_ESCALATION_SIGNAL_LABELS = {
    "legal_or_compliance": "legal/compliance signal",
    "hr_sensitive":        "HR-sensitive signal",
    "financial_dispute":   "financial-dispute signal",
    "brand_or_contract":   "brand/contract signal",
}


def _should_create_zoho_ticket(
    team_key: str,
    priority: Optional[str],
    escalation_signal: str = "none",
    escalation_reason: str = "",
) -> tuple[bool, str]:
    """Return (should_escalate, reason). The reason flows into the private-
    note label so agents see WHY a ticket was raised.

    Pure function: no I/O, no LLM call. Decisions are based on data already
    in scope (team routing + agent-set priority + classifier output)."""
    # 1. Universal high-priority override — bypasses team / signal rules.
    if priority and priority.lower() in config.PRIORITY_ESCALATION_LEVELS:
        return True, f"high_priority({priority})"

    # 2. Legal team always escalates (compliance + audit trail).
    if (team_key or "").lower() == "legal":
        return True, "team_legal"

    # 3. Classifier-detected escalation signal.
    sig = (escalation_signal or "none").lower()
    if sig != "none":
        label = _ESCALATION_SIGNAL_LABELS.get(sig, sig)
        # Quote the model's reason verbatim (truncated) — that's the audit
        # trail compliance will eventually ask for.
        reason_snippet = (escalation_reason or "").strip().replace('"', "'")[:120]
        suffix = f": \"{reason_snippet}\"" if reason_snippet else ""
        return True, f"signal_{sig}({label}{suffix})"

    return False, f"no_escalation_for_team({team_key!r})"


# ── Priority-ticket cooldown helper ───────────────────────────────────────
def _has_recent_priority_ticket(tickets: list, cooldown_minutes: int) -> bool:
    """True if any priority-sourced ticket in `tickets` was created within
    the cooldown window.

    Replaces the "any ticket exists, never escalate again" guard from
    PR #8. That guard stopped the infinite loop but also blocked legitimate
    re-escalations forever — if the AI classifier already raised a
    `auto_signal_*` ticket, an agent bumping priority later would silently
    do nothing.

    The smarter rule: only block when the SAME source-type (priority_*)
    fired recently. That way:
      * AI-signal ticket exists + agent bumps priority → fires NEW priority
        ticket (different source, no conflict).
      * Priority ticket exists + the immediate self-fired
        conversation_updated webhook from our own write → blocked
        (loop guard, fires within milliseconds, well inside window).
      * Conversation resolved months ago, re-opened, re-flagged urgent →
        fires NEW ticket (outside window).

    Defensive on bad timestamps: anything we can't parse is treated as
    RECENT — better to skip a re-escalation than to re-enable the loop.
    """
    if cooldown_minutes <= 0 or not tickets:
        return False
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=cooldown_minutes)
    for t in tickets:
        source = str((t or {}).get("source") or "")
        if not source.startswith("priority_"):
            continue
        created_raw = (t or {}).get("created_at") or ""
        try:
            created = datetime.fromisoformat(created_raw)
        except (TypeError, ValueError):
            # Unparseable timestamp on a priority ticket → treat as recent
            # (loop-safety bias).
            return True
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            return True
    return False


def _priority_changed(data: dict) -> bool:
    """True if this conversation_updated event's `changed_attributes` says
    the priority changed — or if we can't tell.

    Chatwoot's payload shape for changed_attributes varies by version:
      * list of one-key dicts:  [{"priority": {"current_value": "urgent",
                                               "previous_value": None}}]
      * flat dict:              {"priority": ["high", "urgent"], ...}
    Both are handled. A missing/empty key returns True (can't-tell →
    proceed), which degrades gracefully to cooldown-only filtering — the
    exact behaviour before this check existed, never stricter than that.
    """
    changed = data.get("changed_attributes")
    if not changed:
        return True   # can't tell → let the cooldown guard decide
    if isinstance(changed, dict):
        return "priority" in changed
    if isinstance(changed, list):
        return any(
            isinstance(c, dict) and "priority" in c
            for c in changed
        )
    return True       # unknown shape → fail open to cooldown guard


# ── Webhook signature (optional) ──────────────────────────────────────────
def _verify_signature(signature: Optional[str], timestamp: Optional[str], body: bytes):
    if not config.CHATWOOT_WEBHOOK_SECRET:
        return
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="missing signature")
    expected = "sha256=" + hmac.new(
        config.CHATWOOT_WEBHOOK_SECRET.encode(),
        f"{timestamp}.{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="bad signature")


# ── Comment-conversation detection ────────────────────────────────────────
def _is_comment_conversation(conv: dict) -> bool:
    """True for Instagram/Facebook *comment* conversations (someone commenting
    on a post), as opposed to direct messages.

    These are owned entirely by the in-Chatwoot DM bot, which auto-replies
    with a comment-specific prompt (thank / redirect-to-DM / HANDOFF on spam).
    The bridge must NOT create Zoho tickets or run the spam/team-routing
    pipeline for them — otherwise a public comment that transitions to `open`
    (e.g. the bot hands off a spam comment) spawns a support ticket, which is
    exactly the behaviour we want to stop.

    Chatwoot tags the kind on conversation.additional_attributes.type
    (observed values: 'instagram_comment', 'facebook_comment')."""
    t = (conv.get("additional_attributes") or {}).get("type") or ""
    return "comment" in str(t).lower()


# ── Handler: bot handoff → Zoho ticket ────────────────────────────────────
# Fetch the real transcript + an AI summary for a conversation about to be
# ticketed. Both best-effort: returns ([], {}) on failure so create_ticket
# falls back to its existing payload-based behaviour. Centralised so all
# three escalation paths (manual handoff, priority bump, Option-D) produce
# tickets headlined by the customer's actual issue, not the bot's handoff
# line.
async def _ticket_context(conv_id) -> tuple[list, dict]:
    if not conv_id:
        return [], {}
    messages = await chatwoot.get_conversation_messages(conv_id)
    if not messages:
        return messages, {}
    # Conversation-level action (not tied to one incoming message) → span with
    # no message_id, nested under the conversation trace. Created only when the
    # summariser actually runs, so no empty grouping spans.
    _lf = tracing.message_parent(conv_id, name="ticket-summary")
    summary = await summarizer.summarize_conversation(messages, lf_parent=_lf)
    return messages, summary


async def handle_status_changed(data: dict) -> dict:
    conv       = data.get("conversation") or data
    conv_id    = conv.get("id") or data.get("id")
    new_status = (conv.get("status") or data.get("status") or "").lower()
    if new_status == "resolved":
        # Handoff handled → clear the agent-needed markers so the unified section
        # only shows conversations still awaiting a human. Pass the payload conv
        # so only labels actually present get a remove_label call (the frequent
        # auto-resolves that were never handoffs stay cheap).
        removed = await _clear_agent_needed(conv_id, conv)
        return {"handled": "agent_needed_cleared", "removed": removed} if removed \
            else {"ignored": True, "reason": "resolved_no_agent_labels"}
    if new_status != "open":
        return {"ignored": True, "reason": f"status={new_status}"}

    # Fetch the full conversation up front: its meta.channel tells social from
    # email, and its additional_attributes reliably carry the comment marker
    # (the webhook payload often omits it). One API call, only on handoff.
    try:
        full_conv = await chatwoot.get_conversation(conv_id)
    except Exception as e:
        print(f"[handoff] could not load conv {conv_id}: {e} — falling back to Zoho")
        full_conv = conv

    # Google Reviews inbox is handled end-to-end by the reviews poller and must
    # NEVER raise a Zoho ticket. A review conversation reopens whenever the
    # customer EDITS their review (the poller re-surfaces it), which fires this
    # status→open webhook — without this guard that edit would spawn a spurious
    # "Zoho ticket" card on the review. (Same guard as handle_message_created.)
    review_inbox_id = (full_conv.get("inbox_id")
                       or (conv.get("inbox") or {}).get("id"))
    if config.REVIEWS_INBOX_ID and review_inbox_id == config.REVIEWS_INBOX_ID:
        print(f"[handoff] conv {conv_id} is a review — handled by reviews poller, skipping")
        return {"ignored": True, "reason": "reviews_inbox"}

    # Comments are handled by the DM bot — never raise a Zoho ticket or post a
    # DM-style template card on a public comment thread. Check both the payload
    # and the fetched conversation so the marker is never missed.
    if _is_comment_conversation(full_conv) or _is_comment_conversation(conv):
        print(f"[handoff] conv {conv_id} is a comment — handled by DM bot")
        return {"ignored": True, "reason": "comment_conversation"}

    # Social DM (Instagram / Facebook / WhatsApp) handoff: the DM bot just
    # passed the baton to a human. Post the Durian template-suggestion card so
    # the agent gets a ready-to-edit reply — NOT a Zoho ticket.
    channel_type   = ((full_conv.get("meta") or {}).get("channel")) or ""
    social_channel = TEMPLATE_CHANNEL_FOR_INBOX_TYPE.get(channel_type)
    if social_channel:
        return await handle_template_suggest(full_conv, social_channel)

    # Email handoff → Zoho ticket. With the approval gate on, pause for an
    # agent decision instead of auto-creating.
    sender_email = ((full_conv.get("meta") or {}).get("sender") or {}).get("email") or ""
    if config.ZOHO_TICKET_REQUIRE_APPROVAL:
        return await _pause_ticket_for_approval(conv_id, sender_email, "manual_handoff")
    try:
        messages, summary = await _ticket_context(conv_id)
        ticket = await zoho.create_ticket(data, messages=messages, summary=summary)
        await _surface_ticket_in_chatwoot(conv_id, ticket, source="manual_handoff")
        return {"created": True, "ticket_id": ticket.get("id"),
                "ticket_number": ticket.get("ticketNumber")}
    except Exception as e:
        print(f"[handoff] ERROR creating Zoho ticket: {e}")
        return {"created": False, "error": str(e)}


# ── Handler: agent flags conversation high / urgent → create Zoho ticket ──
# Fires from Chatwoot's `conversation_updated` webhook. That event triggers
# on ANY field change (assignee, labels, attributes, priority...), so we
# filter aggressively here.
async def handle_conversation_updated(data: dict) -> dict:
    conv     = data.get("conversation") or data
    conv_id  = conv.get("id") or data.get("id")
    priority = (conv.get("priority") or "").lower()

    if priority not in config.PRIORITY_ESCALATION_LEVELS:
        return {"ignored": True, "reason": f"priority_not_critical({priority!r})"}

    # Intent check: only escalate when THIS event actually changed the
    # priority. conversation_updated fires on EVERY conversation mutation —
    # resolve, label, reassign, snooze, and the bridge's own
    # custom_attributes writes. Without this check, a conversation left
    # sitting at priority=urgent would spawn a fresh ticket on ANY of those
    # updates once the cooldown window lapsed (e.g. "agent resolves a
    # 2-hour-old urgent conversation → surprise new Zoho ticket").
    #
    # Chatwoot includes `changed_attributes` on conversation_updated
    # payloads. Observed shapes vary by version — list of one-key dicts
    # ([{"priority": {...}}]) or a flat dict ({"priority": [old, new]}) —
    # so _priority_changed() handles both. When the key is absent entirely
    # (older Chatwoot / partial payloads) we fall through to the cooldown
    # guard alone, which is exactly the pre-fix behaviour — no worse.
    if not _priority_changed(data):
        return {"ignored": True, "reason": "priority_unchanged"}

    # Smart idempotency — see _has_recent_priority_ticket() for the full
    # design. Short version: PR #8 blocked ALL re-tickets ("any ticket
    # exists, skip"). That fixed the infinite loop but broke the legitimate
    # case of "AI classifier already raised an auto_signal ticket → agent
    # bumps priority → should fire a SECOND, separately-sourced ticket."
    #
    # New rule: only block when a priority_* ticket fired inside the
    # cooldown window (default 60 min, config.PRIORITY_ESCALATION_COOLDOWN_MINUTES).
    # That still kills the self-fired conversation_updated loop (the second
    # webhook arrives in milliseconds, well inside the window) while
    # allowing AI-signal + priority tickets to coexist and re-escalations
    # of stale conversations months later to land fresh tickets.
    custom_attrs = conv.get("custom_attributes") or {}
    zoho_tickets = list(custom_attrs.get("zoho_tickets") or [])
    # Legacy compat: pre-migration conversations only had the singular key.
    if not zoho_tickets and custom_attrs.get("zoho_ticket"):
        legacy = custom_attrs["zoho_ticket"]
        if isinstance(legacy, dict):
            zoho_tickets = [legacy]
    if _has_recent_priority_ticket(
        zoho_tickets, config.PRIORITY_ESCALATION_COOLDOWN_MINUTES
    ):
        return {"ignored": True, "reason": "recent_priority_escalation"}

    print(f"[priority] conv {conv_id} flagged {priority.upper()} — escalating to Zoho")

    # Approval gate: pause for an agent decision instead of auto-creating.
    if config.ZOHO_TICKET_REQUIRE_APPROVAL:
        sender_email = ((conv.get("meta") or {}).get("sender") or {}).get("email") or ""
        return await _pause_ticket_for_approval(
            conv_id, sender_email, f"priority_{priority}")

    sla_hours = config.PRIORITY_SLA_HOURS.get(priority, 24)
    now    = datetime.now(timezone.utc)
    due_at = now + timedelta(hours=sla_hours)

    # NOTE: zoho.create_ticket signature is in this repo — no need to
    # backwards-compat-shim it. Pre-review there was an `except TypeError`
    # fallback to the no-kwargs path, but that quietly swallowed any
    # TypeError raised INSIDE create_ticket (e.g., a None field arithmetic
    # bug), dropping the SLA entirely. Removed.
    try:
        messages, summary = await _ticket_context(conv_id)
        ticket = await zoho.create_ticket(data, priority=priority, due_at=due_at,
                                          messages=messages, summary=summary)
    except Exception as e:
        print(f"[priority] ERROR creating Zoho ticket for conv {conv_id}: {e}")
        return {"created": False, "error": str(e)}

    sla_meta = {
        "level":   priority,
        "hours":   sla_hours,
        "set_at":  now.isoformat(timespec="seconds"),
        "due_at":  due_at.isoformat(timespec="seconds"),
    }
    await _surface_ticket_in_chatwoot(
        conv_id, ticket, source=f"priority_{priority}", sla=sla_meta,
    )
    return {
        "created":       True,
        "ticket_id":     ticket.get("id"),
        "ticket_number": ticket.get("ticketNumber"),
        "priority":      priority,
        "sla":           sla_meta,
    }


def _priority_from_label(label: Optional[str]) -> Optional[str]:
    """If an escalation label encodes a Chatwoot priority ("priority_<level>"),
    return that level ("urgent"/"high"/…) so create_ticket can map it onto the
    Zoho ticket. Returns None for non-priority escalations (complaint, legal,
    etc.) — those keep the default Medium."""
    if label and label.startswith("priority_"):
        return label.split("_", 1)[1] or None
    return None


# ── Helper: visible bubble + sidebar pane data after ticket creation ──────
async def _surface_ticket_in_chatwoot(
    conv_id: Optional[int],
    ticket: dict,
    source: str,
    sla: Optional[dict] = None,
) -> None:
    """After a Zoho ticket is created, do three things in Chatwoot:
      1. Search Zoho for past tickets matching the new subject → surfaced as
         'possibly related' to help spot duplicates.
      2. Post a private note ('🎫 Zoho Desk ticket #X created' + related list
         + optional SLA deadline) so agents see a bubble inline.
      3. Merge ticket metadata + related-ticket list + optional SLA into
         conversation.custom_attributes for the sidebar panels.
    All steps are best-effort and never raise — a ticket exists in Zoho
    either way.
    """
    if not conv_id:
        return
    ticket_id     = ticket.get("id")
    ticket_number = ticket.get("ticketNumber") or ticket.get("ticket_number")
    subject       = ticket.get("subject") or ""
    web_url       = ticket.get("webUrl") or (
        f"{config.ZOHO_DESK_URL}/agent/tickets/details/{ticket_id}"
        if ticket_id else None
    )

    # Render the escalation source as a human-readable suffix on the note.
    # `source` shapes (set by callers):
    #   "manual_handoff"                            (handle_status_changed)
    #   "priority_<level>"                          (handle_conversation_updated)
    #   "auto_high_priority(<level>)"               (Option-D priority)
    #   "auto_team_legal"                           (Option-D legal-team always)
    #   "auto_signal_<sig>(<label>: \"<reason>\")"  (Option-D classifier signal)
    #   "attached_to_existing(<escalation_label>)"  (ticket-dedup attach path)
    raw = source or ""
    is_attach = raw.startswith("attached_to_existing")

    if raw == "manual_handoff":
        label_source = " (manual handoff)"
    elif raw.startswith("auto_high_priority"):
        label_source = " (high-priority escalation)"
    elif raw == "auto_team_legal":
        label_source = " (auto-routed: Legal)"
    elif raw.startswith("auto_signal_"):
        # The classifier already produced a human-friendly summary inside
        # the parens — surface it verbatim so the agent sees WHY the AI
        # escalated, not just an opaque enum.
        if "(" in raw and raw.endswith(")"):
            inner = raw[raw.index("(") + 1: -1]
        else:
            inner = raw[len("auto_signal_"):]
        label_source = f" (AI escalation: {inner})"
    elif raw.startswith("priority_"):
        level = raw.split("_", 1)[1] if "_" in raw else raw
        label_source = f" (🚨 priority escalation: {level.upper()})"
    elif is_attach:
        # Render the inner escalation label via the pretty-name lookup so
        # the agent reads "Legal / compliance" instead of "team_legal".
        if "(" in raw and raw.endswith(")"):
            inner = raw[raw.index("(") + 1: -1]
        else:
            inner = ""
        pretty = _ESCALATION_LABEL_PRETTY.get(inner, inner.replace("_", " ").title())
        label_source = f" ({pretty})" if pretty else ""
    elif raw.startswith("auto_priority_"):
        level = raw[len("auto_priority_"):]
        label_source = f" (🚨 auto-escalated: {level.upper()} priority)"
    elif raw.startswith("auto_"):
        # Generic auto-escalation (auto_complaint, auto_legal_complaint, …) —
        # prettify the inner label instead of dumping the raw enum.
        inner = raw[len("auto_"):]
        pretty = _ESCALATION_LABEL_PRETTY.get(inner, inner.replace("_", " ").title())
        label_source = f" (auto-escalated: {pretty})"
    else:
        label_source = f" ({raw})" if raw else ""

    # Related-ticket search makes sense ONLY when a new ticket was just
    # created — for the attach path the agent has already picked their
    # target from the dedup panel, so a "possibly related tickets" list
    # below would be redundant noise.
    if is_attach:
        related = []
    else:
        # 1. Hunt for related tickets (best-effort)
        related = await zoho.search_tickets(subject, exclude_id=ticket_id, limit=3)
        if related:
            print(f"[zoho] found {len(related)} related tickets for conv {conv_id}: "
                  + ", ".join(f"#{r.get('number') or r.get('id')}" for r in related))
        else:
            print(f"[zoho] no related tickets found for conv {conv_id} "
                  f"(subject={subject[:60]!r})")

    # 2. Compose the private note. Header differs for the attach path:
    # the ticket wasn't created just now — the agent linked this conv to
    # an existing ticket — so saying "ticket created" would be misleading.
    if is_attach:
        note = f"🔗 **{config.PRODUCT_NAME} — conversation attached to Zoho Desk ticket**"
    else:
        note = f"🎫 **{config.PRODUCT_NAME} — Zoho Desk ticket created**"
    if ticket_number:
        note += f" — [#{ticket_number}]({web_url})" if web_url else f" — #{ticket_number}"
    elif ticket_id:
        note += f" — [{ticket_id}]({web_url})" if web_url else f" — {ticket_id}"
    note += label_source

    if related:
        bullets = "\n".join(
            f"• [#{r.get('number') or r.get('id')}]({r.get('url')}) — "
            f"{(r.get('subject') or '')[:80]}"
            for r in related
        )
        note += f"\n\n_Possibly related tickets:_\n{bullets}"

    if sla and sla.get("due_at"):
        try:
            due_dt    = datetime.fromisoformat(sla["due_at"])
            due_local = due_dt.astimezone().strftime("%b %d, %H:%M %Z")
        except (ValueError, TypeError):
            due_local = sla["due_at"]
        note += (
            f"\n\n⏱ **Needs reply by {due_local}** "
            f"(SLA: {sla.get('hours')}h for {sla.get('level','').upper()})"
        )

    try:
        await chatwoot.post_private_note(conv_id, note)
    except Exception as e:
        print(f"[zoho] post_private_note failed for conv {conv_id}: {e}")

    # 3. Persist structured data on the conversation.
    #
    # A conversation can spawn MULTIPLE Zoho tickets over its lifetime
    # (re-opens, priority bumps, new escalations weeks apart, separate
    # issues raised in the same thread). Persist the full history as an
    # array, newest first, deduped by ticket id, capped at MAX_TRACKED.
    #
    # `zoho_tickets` is the single source of truth. The legacy `zoho_ticket`
    # (singular) key is no longer written — a one-shot backfill script
    # (scripts/backfill_zoho_tickets.py) migrates any existing conversations
    # that still have the singular key into the array and removes it.
    # The read-side legacy seed below is kept as a defense-in-depth measure
    # for any conversation the backfill missed (created between deploy and
    # script run, etc.) — costs ~5 lines and prevents silent data loss.
    new_entry = {
        "id":         ticket_id,
        "number":     ticket_number,
        "url":        web_url,
        "subject":    subject,
        "source":     source,
        "created_at": _now_iso(),
        "status":     ticket.get("status") or "Open",  # last known; refresh via sync job
    }

    # Read existing tickets array. Best-effort: if the fetch fails (network,
    # 4xx, conv vanished), we treat the array as empty rather than blocking
    # the whole surface step — the new ticket still lands as the head.
    MAX_TRACKED = 20
    existing_tickets: list[dict] = []
    try:
        conv_data = await chatwoot.get_conversation(conv_id)
        existing_attrs = conv_data.get("custom_attributes") or {}
        existing_tickets = existing_attrs.get("zoho_tickets") or []
        # Defense-in-depth: if a conversation was missed by the backfill
        # script and still has the legacy singular key, fold it in so the
        # old ticket isn't lost from history on the next write.
        if not existing_tickets and existing_attrs.get("zoho_ticket"):
            legacy = existing_attrs["zoho_ticket"]
            if isinstance(legacy, dict) and legacy.get("id"):
                existing_tickets = [legacy]
    except Exception as e:
        print(f"[zoho] get_conversation failed for {conv_id}: {e} — "
              f"proceeding with empty tickets history")

    # Dedup by id (idempotency — same webhook can fire twice); keep the
    # latest version of any duplicate, place it at the head.
    deduped = [t for t in existing_tickets
               if str(t.get("id") or "") != str(ticket_id or "")]
    tickets_array = [new_entry] + deduped
    tickets_array = tickets_array[:MAX_TRACKED]

    payload_attrs = {
        "zoho_tickets":    tickets_array,
        "related_tickets": related,
    }
    if sla:
        payload_attrs["priority_sla"] = sla
    try:
        await chatwoot.merge_custom_attributes(conv_id, payload_attrs)
    except Exception as e:
        print(f"[zoho] merge_custom_attributes failed for conv {conv_id}: {e}")


# ── Ticket dedup: pause auto-create when contact has open ticket ──────────
# Flow:
#   1. handle_message_created decides this conv warrants a Zoho ticket.
#   2. Before calling zoho.create_ticket, bridge calls
#      zoho.search_open_tickets_by_email — if any open tickets exist for the
#      contact, this function sets conversation.custom_attributes.pending_zoho_ticket
#      (read by PendingTicketDecisionPanel in the sidebar) and posts a
#      private note. NO ticket gets created until an agent decides.
#   3. Agent clicks [Attach to #N] or [Create new] in the sidebar.
#   4. /chatwoot/resolve-ticket-decision (below) acts on that choice.
#
# Idempotency: if pending_zoho_ticket is already set on this conv we don't
# overwrite it — the existing decision context is preserved verbatim so
# subsequent webhook fires don't add noise.
async def _pause_ticket_for_approval(conv_id: int, sender_email: str,
                                     escalation_label: str) -> dict:
    """Look up any open tickets for the contact, then pause for an agent
    decision (Approve / Attach-to-existing / Reject). Used by the direct-create
    escalation paths (manual handoff, priority bump) when
    ZOHO_TICKET_REQUIRE_APPROVAL is on, so no ticket is created without a human.
    Returns a small JSON-friendly dict for the handler to relay back."""
    candidates = []
    if sender_email:
        try:
            candidates = await zoho.search_open_tickets_by_email(sender_email, limit=5)
        except Exception as e:
            print(f"[zoho] open-ticket lookup failed for {sender_email!r}: {e}")
    await _pause_for_agent_decision(
        conv_id=conv_id, sender_email=sender_email,
        escalation_label=escalation_label, candidates=candidates,
    )
    return {"created": False, "awaiting_approval": True,
            "candidates": [t.get("id") for t in candidates],
            "escalation_label": escalation_label}


async def _pause_for_agent_decision(conv_id: int,
                                    sender_email: str,
                                    escalation_label: str,
                                    candidates: list[dict],
                                    attach_only: bool = False) -> None:
    """Write pending_zoho_ticket onto the conversation + post a private note.

    Best-effort: failure here means an agent might not see the banner, but
    the bridge will still skip ticket creation — better than blocking the
    webhook with an exception."""
    # Read-then-write: if a previous webhook already paused this conv, leave
    # the existing pending state alone so the candidates list stays stable.
    try:
        conv_data = await chatwoot.get_conversation(conv_id)
        existing = (conv_data.get("custom_attributes") or {}).get("pending_zoho_ticket")
        if existing:
            print(f"[zoho-dedup] conv {conv_id}: pending decision already set "
                  f"({len(existing.get('candidates') or [])} candidates) — leaving as-is")
            return
    except Exception as e:
        print(f"[zoho-dedup] get_conversation failed for {conv_id}: {e} — "
              f"continuing with fresh pending state")

    pending = {
        "sender_email":     sender_email,
        "escalation_label": escalation_label,
        "candidates":       candidates,
        "attach_only":      attach_only,
        "suggested_at":     _now_iso(),
    }
    try:
        await chatwoot.merge_custom_attributes(conv_id, {"pending_zoho_ticket": pending})
    except Exception as e:
        print(f"[zoho-dedup] merge_custom_attributes failed for {conv_id}: {e}")

    # Ticket creation is now waiting on a human → surface in the unified
    # "Agent needs" section (Email channel). Cleared in _resolve_ticket_decision.
    await _flag_agent_needed(conv_id, "email")

    # Post a private note so agents notice without watching the sidebar. The
    # wording differs for the two pause reasons: a possible duplicate (open
    # tickets exist) vs. the approval gate (no duplicate, just needs sign-off).
    lines = [
        f"🎫 **{config.PRODUCT_NAME} — Zoho ticket creation paused, agent decision needed**",
        "",
    ]
    if candidates:
        # attach_only: the customer named an existing ticket, so this is a
        # follow-up on THAT ticket — the agent attaches, never creates a
        # duplicate. The plain path is a possible-duplicate judgement call.
        if attach_only:
            lines.append(
                f"This message from {sender_email} refers to an existing Zoho "
                f"ticket. Attach this conversation to it rather than creating a "
                f"new one.")
        else:
            lines.append(
                f"Existing Zoho tickets look related to this message from "
                f"{sender_email}. The bridge would have escalated this "
                f"conversation as **{escalation_label}**, but it's waiting for "
                f"you to decide.")
        lines.append("")
        lines.append("Possibly related tickets:")
        for t in candidates[:5]:
            num = f"#{t.get('number')}" if t.get("number") else (t.get("id") or "?")
            subj = (t.get("subject") or "")[:80]
            url = t.get("url")
            entry = f"- [{num}]({url}) — {subj}" if url else f"- {num} — {subj}"
            # Say WHY it surfaced (same contact / #N referenced / similar
            # content) + status, so the agent can judge a cross-contact match
            # — e.g. the same person writing from a second email address.
            why = t.get("match")
            status = t.get("status")
            tag = ", ".join(x for x in (why, status) if x)
            lines.append(f"{entry} _({tag})_" if tag else entry)
        lines.append("")
        lines.append("Attach this conversation to the referenced ticket, or reject:"
                     if attach_only else
                     "Attach to one of the above, create a new ticket, or reject:")
        lines.append("")
        lines.append("[**Open the Ticket decision panel →**](#cw-panel/ticket-decision)")
    else:
        lines.append(
            f"This conversation was flagged for a Zoho ticket "
            f"(**{escalation_label}**). No ticket has been created yet — "
            f"approve to create it, or reject:")
        lines.append("")
        lines.append("[**Open the Ticket decision panel →**](#cw-panel/ticket-decision)")
    try:
        await chatwoot.post_private_note(conv_id, "\n".join(lines))
    except Exception as e:
        print(f"[zoho-dedup] post_private_note failed for {conv_id}: {e}")


async def _resolve_ticket_decision(conv_id: int, choice: str,
                                   target_ticket_id: Optional[str] = None) -> dict:
    """Execute the agent's choice on a paused ticket. Idempotent on success
    (clears pending_zoho_ticket regardless of which branch ran). Returns a
    small JSON-friendly dict for the HTTP endpoint to relay back."""
    if choice not in ("use_existing", "create_new", "reject"):
        raise ValueError(f"invalid choice: {choice!r}")

    # Pull the paused-decision context off the conversation.
    conv_data = await chatwoot.get_conversation(conv_id)
    attrs = conv_data.get("custom_attributes") or {}
    pending = attrs.get("pending_zoho_ticket") or {}
    if not pending:
        return {"resolved": False, "reason": "no_pending_decision"}

    escalation_label = pending.get("escalation_label") or "manual_handoff"
    candidates       = pending.get("candidates") or []
    sender           = (conv_data.get("meta") or {}).get("sender") or {}
    sender_email     = pending.get("sender_email") or sender.get("email") or ""
    customer_name    = _resolve_customer_name(sender.get("name") or "", sender_email)

    # We reconstruct a synthetic webhook payload from the live conversation
    # so the existing create_ticket + _surface_ticket_in_chatwoot helpers
    # work unchanged. Cheaper than refactoring those to accept a Conversation
    # record directly.
    synthetic_payload = {"conversation": conv_data}

    result: dict = {"resolved": True, "choice": choice}

    if choice == "reject":
        # Agent declined the ticket — create nothing, just record the decision.
        print(f"[zoho] conv {conv_id}: agent REJECTED ticket creation "
              f"({escalation_label})")
        try:
            await chatwoot.post_private_note(
                conv_id,
                "🚫 **Zoho ticket creation rejected** — no ticket was created "
                "for this conversation.",
            )
        except Exception as e:
            print(f"[zoho] reject note failed for conv {conv_id}: {e}")
        result["rejected"] = True

    elif choice == "create_new":
        try:
            messages, summary = await _ticket_context(conv_id)
            # Complaint tickets carry the client's dedicated owner — preserve
            # that when the paused complaint path lands here (the direct
            # auto-create in _maybe_create_complaint_ticket does the same).
            assignee_id = (config.COMPLAINT_TICKET_OWNER_DESK_ID or None) \
                if escalation_label == "complaint" else None
            cdetails = None
            if escalation_label == "complaint":
                try:
                    cdetails = ((await chatwoot.get_conversation(conv_id)).get(
                        "custom_attributes") or {}).get("complaint_details") or None
                except Exception:
                    pass
            ticket = await zoho.create_ticket(
                synthetic_payload, messages=messages, summary=summary,
                priority=_priority_from_label(escalation_label),
                assignee_id=assignee_id, complaint_details=cdetails,
            )
            print(f"[zoho-dedup] conv {conv_id}: agent chose CREATE_NEW → "
                  f"ticket {ticket.get('id')}")
            await _surface_ticket_in_chatwoot(
                conv_id, ticket, source=f"auto_{escalation_label}"
            )
            # The paused-complaint customer got a number-less ack at pause
            # time — deliver their reference number now the ticket exists.
            if escalation_label == "complaint":
                await _send_complaint_reference_ack(
                    conv_id, sender_email, customer_name,
                    ticket.get("ticketNumber"))
            result["ticket_id"] = ticket.get("id")
        except Exception as e:
            print(f"[zoho-dedup] create_new path failed for conv {conv_id}: {e}")
            result["resolved"] = False
            result["error"]    = str(e)

    elif choice == "use_existing":
        target = next(
            (c for c in candidates if str(c.get("id")) == str(target_ticket_id)),
            None,
        )
        if not target:
            return {"resolved": False, "reason": "target_not_in_candidates"}
        ticket_id = str(target.get("id"))
        try:
            messages, summary = await _ticket_context(conv_id)
            comment_html = _format_attach_comment(
                conv_id          = conv_id,
                escalation_label = escalation_label,
                summary          = summary,
                messages         = messages,
            )
            # is_public=False keeps the comment internal (the "Private" badge
            # in Zoho's UI). All agents in the org see it regardless of team;
            # only the customer is excluded. Public would risk emailing the
            # customer their own complaint summary back, depending on Zoho's
            # workflow rules.
            await zoho.add_comment_to_ticket(ticket_id, comment_html, is_public=False)
            # Synthesize a ticket-like dict so _surface_ticket_in_chatwoot
            # can post the standard confirmation + update the sidebar.
            ticket_for_surface = {
                "id":           ticket_id,
                "ticketNumber": target.get("number"),
                "subject":      target.get("subject") or "",
                "status":       target.get("status") or "Open",
                "webUrl":       target.get("url"),
            }
            await _surface_ticket_in_chatwoot(
                conv_id, ticket_for_surface,
                source=f"attached_to_existing({escalation_label})",
            )
            # Attaching links this conversation to an existing ticket — give
            # the customer that ticket's number as their reference too.
            if escalation_label == "complaint":
                await _send_complaint_reference_ack(
                    conv_id, sender_email, customer_name, target.get("number"))
            print(f"[zoho-dedup] conv {conv_id}: agent chose USE_EXISTING → "
                  f"appended comment to ticket {ticket_id}")
            result["ticket_id"] = ticket_id
        except Exception as e:
            print(f"[zoho-dedup] use_existing path failed for conv {conv_id} "
                  f"ticket {ticket_id}: {e}")
            result["resolved"] = False
            result["error"]    = str(e)

    # Clear the pending flag whether or not the action succeeded — leaving
    # it set would block subsequent webhooks. On error, the agent can still
    # re-trigger via a manual handoff.
    try:
        await chatwoot.merge_custom_attributes(
            conv_id, {"pending_zoho_ticket": None}
        )
    except Exception as e:
        print(f"[zoho-dedup] failed to clear pending flag on {conv_id}: {e}")

    # Agent acted on the ticket decision → drop it from the "Agent needs" section.
    await _clear_agent_needed(conv_id)

    return result


# ── Human-in-the-loop email category decision ─────────────────────────────
# When the categoriser's confidence is below CATEGORY_AUTO_CONFIDENCE, the
# bridge does NOT forward. It writes `pending_category_decision` onto the
# conversation + posts a private note. The sidebar Category Decision panel
# reads that attribute and lets an agent confirm the AI's pick (or choose
# another from the dropdown). The choice POSTs to the Rails proxy →
# /chatwoot/resolve-category-decision → _resolve_category_decision, which
# then runs the real forward/route action for the chosen category.

# The ONE label for every human-in-the-loop case (category/sector confirmation
# AND region review). Colour it red in Chatwoot → Settings → Labels. The
# permanent "Needs review" sidebar view filters on it; removed automatically
# once the agent confirms. (Previously each review kind ALSO got its own
# specific label — needs-category-review / needs-region-review — but the double
# chips confused agents, so everything is unified on this single label.)
NEEDS_REVIEW_UMBRELLA_LABEL = "needs-review"


def _first_incoming_content(messages: list) -> str:
    for m in messages or []:
        if m.get("message_type") in (0, "incoming") and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


async def _post_category_decision(conv_id: int, category_result: dict,
                                  sector_review_only: bool = False) -> dict:
    """Write the pending decision + post the agent-facing card. Idempotent.

    sector_review_only: the category is already confident (project_bulk_order)
    but the buyer sector (government/private) is uncertain — the card is shown
    so the agent confirms the sector before it forwards."""
    try:
        existing = (await chatwoot.get_conversation(conv_id)).get(
            "custom_attributes", {}).get("pending_category_decision")
    except Exception:
        existing = None
    if existing:
        return {"ignored": True, "reason": "category_decision_already_pending"}

    cat = category_result.get("category")
    # On the fallback band the classifier overwrites its pick with "fallback"
    # but keeps the original guess in raw_category — show that to the agent.
    suggested = category_result.get("raw_category") if cat == "fallback" else cat
    conf   = float(category_result.get("confidence") or 0)
    reason = category_result.get("reason") or ""
    alts = [
        {"category": a["category"],
         "display_name": classifier.category_display_name(a["category"]),
         "confidence": round(float(a.get("confidence") or 0), 2)}
        for a in (category_result.get("alternatives") or [])
        if a.get("category")
    ]

    pending = {
        "suggested":         suggested,
        "suggested_display": classifier.category_display_name(suggested) if suggested else "",
        "confidence":        round(conf, 2),
        "reason":            reason,
        "alternatives":      alts,
        "categories":        classifier.category_choices(),   # full dropdown
        "suggested_at":      _now_iso(),
    }
    # Bulk orders also need the buyer sector (government/private). Surface the
    # AI's sector guess so the same card can show a sector picker; the panel
    # offers the two sector choices itself.
    if suggested == "project_bulk_order" and (category_result.get("rule") or {}).get("sector_routing"):
        pending["needs_sector"]      = True
        pending["sector_suggested"]  = category_result.get("sector")
        pending["sector_confidence"] = round(float(category_result.get("sector_confidence") or 0), 2)
        pending["sector_reason"]     = category_result.get("sector_reason") or ""
        pending["sector_review_only"] = bool(sector_review_only)
    try:
        await chatwoot.merge_custom_attributes(
            conv_id, {"pending_category_decision": pending})
    except Exception as e:
        print(f"[category-decision] merge_custom_attributes failed: {e}")

    pct = int(round(conf * 100))
    if sector_review_only:
        ssec  = pending.get("sector_suggested") or "unknown"
        sspct = int(round(float(pending.get("sector_confidence") or 0) * 100))
        lines = [
            "🏛️ **Bulk order — confirm the buyer sector**",
            "",
            f"This is clearly a **Project / Bulk Order**, but I'm not sure whether "
            f"the buyer is **government** or **private** (best guess: **{ssec}**, "
            f"{sspct}% confident). They route to different handlers, so nothing "
            f"has been forwarded yet.",
        ]
        if pending.get("sector_reason"):
            lines.append(f"_{pending['sector_reason']}_")
    else:
        lines = [
            "🤔 **Low-confidence classification — needs your confirmation**",
            "",
            f"Best guess: **{pending['suggested_display']}** ({pct}% confident). "
            f"That's below the auto-forward bar, so nothing has been sent yet.",
        ]
        if reason:
            lines.append(f"_{reason}_")
        if alts:
            lines.append("")
            lines.append("Other possibilities: " + ", ".join(
                f"{a['display_name']} ({int(round(a['confidence'] * 100))}%)" for a in alts))
    lines += [
        "",
        "Confirm in the panel and it'll be forwarded and routed:",
        "",
        "[**Open the Category decision panel →**](#cw-panel/category-decision)",
    ]
    try:
        await chatwoot.post_private_note(conv_id, "\n".join(lines))
    except Exception as e:
        print(f"[category-decision] post_private_note failed: {e}")

    # Visible flag in the conversation list so agents can spot these at a
    # glance. Backs the permanent "Needs review" sidebar view.
    try:
        await chatwoot.add_label(conv_id, NEEDS_REVIEW_UMBRELLA_LABEL)
    except Exception as e:
        print(f"[category-decision] add_label({NEEDS_REVIEW_UMBRELLA_LABEL}) failed: {e}")

    # ALSO surface in the unified "Agent needs" section (Email channel) — the
    # category call is a human decision like any other. `needs-review` above keeps
    # its own dedicated view; this umbrella is what makes it show in the unified
    # All/Email view. Cleared when the agent confirms the category (see
    # _resolve_category_decision, which re-flags only if the pick is a deal vertical).
    await _flag_agent_needed(conv_id, "email")

    print(f"[category-decision] conv {conv_id}: card posted "
          f"(suggested={suggested!r} conf={conf})")
    return {"category_decision": True, "suggested": suggested, "confidence": conf}



async def _post_bulk_region_review(conv_id: int, category_result: dict) -> dict:
    """Bulk order whose state/region is unclear (or a state with no configured
    handler). Don't forward to a guessed regional desk — leave it in-channel
    with a note + label so an agent routes it. Sector-aware: government and
    private bulk orders both have region routing, so the note names the actual
    sector instead of assuming private (which mislabelled govt orders)."""
    region = category_result.get("region") or "unclear"
    rconf  = int(round(float(category_result.get("region_confidence") or 0) * 100))
    reason = category_result.get("region_reason") or ""
    sector = (category_result.get("sector") or "private").lower()
    sector_label = "government" if sector == "government" else "private"
    lines = [
        f"📍 **{sector_label.capitalize()} bulk order — region needs an agent decision**",
        "",
        f"This is a **{sector_label} project / bulk order**, but I couldn't "
        "confidently place the customer's state among the regions we route "
        "automatically. It has **not** been "
        f"forwarded — best guess was **{region}** ({rconf}%).",
    ]
    if reason:
        lines.append(f"_{reason}_")
    lines += ["", "→ Please route this to the right regional team manually."]
    try:
        await chatwoot.post_private_note(conv_id, "\n".join(lines))
    except Exception as e:
        print(f"[bulk-region] post_private_note failed for conv {conv_id}: {e}")
    try:
        await chatwoot.add_label(conv_id, NEEDS_REVIEW_UMBRELLA_LABEL)
    except Exception as e:
        print(f"[bulk-region] add_label({NEEDS_REVIEW_UMBRELLA_LABEL}) failed for conv {conv_id}: {e}")
    print(f"[bulk-region] conv {conv_id}: region unclear "
          f"({region} {rconf}%) — left in-channel for agent")
    return {"classified_email_type": "project_bulk_order", "region_review": True}


async def _resolve_category_decision(conv_id: int, category: str,
                                     sector: Optional[str] = None,
                                     agent_name: str = "") -> dict:
    """Agent confirmed a category — run the real forward/route action for it
    and clear the pending flag. Returns a JSON-friendly dict for the endpoint.

    `agent_name` (the agent who confirmed, supplied by the Rails proxy from
    Current.user) is recorded in the audit note and the send is tagged
    `manually-sent` (not `auto-forwarded`) so agent-actioned conversations are
    distinguishable from fully-automatic ones."""
    conv = await chatwoot.get_conversation(conv_id)
    if not (conv.get("custom_attributes") or {}).get("pending_category_decision"):
        return {"resolved": False, "reason": "no_pending_category_decision"}

    rule = (classifier._ROUTING_RULES.get("categories") or {}).get(category)
    if not rule:
        return {"resolved": False, "reason": f"unknown_category:{category}"}

    # Bulk orders: the agent also picked the buyer sector → point the forward at
    # that sector's handler (copy the rule so the shared rules dict is untouched).
    chosen_sector = None
    if category == "project_bulk_order" and sector in ("government", "private"):
        sroute = (rule.get("sector_routing") or {}).get(sector) or {}
        rule = {
            **rule,
            "forward_to": sroute.get("forward_to") or rule.get("forward_to"),
            "cc":         sroute.get("cc") or rule.get("cc") or [],
        }
        chosen_sector = sector

    sender       = (conv.get("meta") or {}).get("sender") or {}
    sender_email = sender.get("email") or ""
    sender_name  = sender.get("name") or ""
    messages     = await chatwoot.get_conversation_messages(conv_id)
    content      = _first_incoming_content(messages)
    additional   = conv.get("additional_attributes") or {}
    subject      = (additional.get("mail_subject") or additional.get("subject")
                    or content[:80])

    category_result = {
        "category":   category,
        "action":     rule.get("action", "in_channel"),
        "confidence": 1.0,
        "reason":     "Confirmed by agent.",
        "rule":       rule,
    }
    if chosen_sector:
        category_result["sector"] = chosen_sector
        category_result["sector_confidence"] = 1.0
        category_result["sector_reason"] = "Confirmed by agent."

    # Persist the agent's decision onto email_category_v2 — the CRM panel
    # gates its Create Deal button on the stored category, and the deal-owner
    # resolver trusts an agent-confirmed sector (confidence 1.0) so the agent
    # is never asked to pick government/private twice.
    try:
        existing_v2 = (conv.get("custom_attributes") or {}).get("email_category_v2") or {}
        v2_update = {**existing_v2, "category": category,
                     "display_name": rule.get("display_name") or category,
                     "confidence": 1.0, "reason": "Confirmed by agent."}
        if chosen_sector:
            v2_update.update({"sector": chosen_sector, "sector_confidence": 1.0,
                              "sector_reason": "Confirmed by agent."})
        await chatwoot.merge_custom_attributes(
            conv_id, {"email_category_v2": v2_update})
    except Exception as e:
        print(f"[category-decision] email_category_v2 merge failed: {e}")

    # Carry the spam/intent label (automated / promotional / …) through so the
    # action layer can suppress the customer acknowledgment for automated mail
    # on the agent-confirmed path too — not just the auto path.
    email_category = (conv.get("custom_attributes") or {}).get("email_category") or ""

    # The category decision itself is now handled → drop the agent-needed marker.
    # _phase2_execute_actions below RE-flags it only when the confirmed category
    # is a deal vertical (Create Deal still pending) or a paused ticket, so a
    # plain routed email leaves the section while a deal lead stays in it.
    await _clear_agent_needed(conv_id, conv)

    action_section = []
    try:
        action_section = await _phase2_execute_actions(
            conv_id=conv_id, category_result=category_result, rule=rule,
            sender_name=sender_name, sender_email=sender_email,
            original_content=content, original_subject=subject or "",
            manual=True, email_category=email_category,
        )
    except Exception as e:
        print(f"[category-decision] action layer failed for conv {conv_id}: {e}")
        action_section = [f"⚠️ Action layer error: `{e}`"]

    display = rule.get("display_name") or category
    by = agent_name.strip() if agent_name and agent_name.strip() else "an agent"
    note = [f"✅ **Marked as {display} by {by}**"]
    if action_section:
        note.append("")
        note.extend(action_section)
    try:
        await chatwoot.post_private_note(conv_id, "\n".join(note))
        await chatwoot.add_label(conv_id, category.replace("_", "-"))
        # Agent-actioned → goes in the "Manually sent" sidebar view (not
        # auto-forwarded). Applied here too so in-channel categories (which
        # don't forward) still land in the view.
        await chatwoot.add_label(conv_id, "manually-sent")
        await chatwoot.remove_label(conv_id, NEEDS_REVIEW_UMBRELLA_LABEL)  # resolved
        team_id = rule.get("team_id")
        if team_id:
            await chatwoot.assign_team(conv_id, int(team_id))
    except Exception as e:
        print(f"[category-decision] post-confirm surfacing failed: {e}")

    try:
        await chatwoot.merge_custom_attributes(
            conv_id, {"pending_category_decision": None})
    except Exception as e:
        print(f"[category-decision] failed to clear pending flag: {e}")

    print(f"[category-decision] conv {conv_id}: confirmed → {category}")
    return {"resolved": True, "category": category}


_ESCALATION_LABEL_PRETTY = {
    "legal_or_compliance": "Legal / compliance",
    "hr_sensitive":        "HR-sensitive",
    "financial_dispute":   "Financial dispute",
    "brand_or_contract":   "Brand / contract",
    "team_legal":          "Legal / compliance",
    "team_hr":             "HR-sensitive",
    "team_marketing":      "Brand / contract",
    "team_support":        "Support",
    "manual_handoff":      "Manual handoff",
}


def _format_attach_comment(conv_id: int,
                           escalation_label: str,
                           summary: Optional[dict],
                           messages: Optional[list]) -> str:
    """Render the Zoho comment body for the 'attach to existing ticket' path.

    Visual style mirrors the AI-generated summary that already appears as the
    ticket's first thread — bulleted sections with bold field labels — so an
    agent reading the ticket sees a consistent layout."""
    conv_url = (
        f"{config.CHATWOOT_PUBLIC_URL.rstrip('/')}"
        f"/app/accounts/{config.CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}"
    )
    label_pretty = _ESCALATION_LABEL_PRETTY.get(
        escalation_label, escalation_label.replace("_", " ").title()
    )

    parts = [
        "<p><b>📨 Another conversation attached from Chatwoot</b></p>",
        "<ul>",
        f"<li><b>Source:</b> "
        f"<a href='{html.escape(conv_url)}'>Open conversation #{conv_id} in Chatwoot ↗</a></li>",
        f"<li><b>Escalation type:</b> {html.escape(label_pretty)}</li>",
        "</ul>",
    ]

    if summary and (summary.get("summary") or summary.get("customer_goal") or summary.get("next_step")):
        parts.append("<p><b>📋 Summary (AI-generated)</b></p><ul>")
        if summary.get("summary"):
            parts.append(f"<li><b>What happened:</b> {html.escape(summary['summary'])}</li>")
        if summary.get("customer_goal"):
            parts.append(f"<li><b>Customer wants:</b> {html.escape(summary['customer_goal'])}</li>")
        if summary.get("next_step"):
            parts.append(f"<li><b>Suggested next step:</b> {html.escape(summary['next_step'])}</li>")
        parts.append("</ul>")

    if messages:
        recent = messages[-6:]
        parts.append("<p><b>💬 Recent messages</b></p>")
        for m in recent:
            role = "Customer" if m.get("message_type") in (0, "incoming") else "Agent"
            body = _format_message_body((m.get("content") or "").strip())
            if not body:
                continue
            # Each message as its own block: bold role on the first line,
            # then the body (with newlines preserved as <br/>) on the next.
            # Lighter visual frame via blockquote — Zoho's editor renders it
            # with a subtle left indent which separates the messages nicely.
            parts.append(
                f"<blockquote><b>{role}:</b><br/>{body}</blockquote>"
            )

    return "\n".join(parts)


def _format_message_body(content: str, max_len: int = 1000) -> str:
    """Render a single message body for the Zoho comment.

    - Strips a leading 'Subject: ...' line that Chatwoot prefixes onto
      outgoing email replies — that's email metadata, not body content,
      and embedding it in the comment text reads as noise.
    - Preserves paragraph breaks (\\n) as <br/> — HTML collapses raw
      newlines to spaces otherwise, which is what made the original
      formatting wall-of-text-y.
    - Truncates at a word boundary near max_len with an ellipsis so we
      don't cut mid-word.
    Returns '' for content that is empty after stripping."""
    s = content.strip()
    if not s:
        return ""
    # Strip a leading "Subject: …\n" line if present.
    if s.lower().startswith("subject:"):
        nl = s.find("\n")
        if nl == -1:
            # whole content is just a subject — nothing useful for the body
            return ""
        s = s[nl + 1:].lstrip()
    # Truncate cleanly at the nearest space before max_len.
    if len(s) > max_len:
        cut = s.rfind(" ", 0, max_len)
        s = s[:cut if cut > 0 else max_len].rstrip() + "…"
    return html.escape(s).replace("\n", "<br/>")


# ── Phase 2A: dry-run preview of the auto-acknowledge / auto-forward flow ─
# Phase 2A is the "say what we'd do, don't actually do it" step between the
# Phase 1 categorizer (observe-only) and the Phase 2B action layer (which
# actually sends emails). It renders:
#
#   - The acknowledgment template that would go to the customer, with
#     the contact's real name substituted (defeats the [Customer's Name]
#     placeholder bug we already fixed on the Copilot side).
#   - For `forward` categories: the routing destination (TO + CC + BCC)
#     and whether the customer would be CC'd, all from the YAML rule.
#
# The result is appended to the categorizer's existing private note so
# agents can sanity-check the plan on real-world emails before we flip
# PHASE_2_DRY_RUN=false and the bridge starts sending.

# Phase 2 behavioural switch.
#   true  (default) — dry-run: render a private-note preview of what
#                     would be sent, nothing actually goes out.
#   false           — Phase 2B: bridge actually sends the customer
#                     acknowledgment + (for forward categories) the
#                     forwarded email to the department address. The
#                     private note shifts to a short "action taken"
#                     audit summary instead of the dry-run preview.
_PHASE_2_DRY_RUN = os.environ.get("PHASE_2_DRY_RUN", "true").lower() != "false"

# Customer-acknowledgment gate (independent of the dry-run flag above).
#   true            — bridge sends the templated acknowledgment to the
#                     customer (the existing behaviour from the spec).
#   false (default) — bridge SKIPS the acknowledgment entirely. Forwards
#                     to departments still run when in non-dry-run mode.
# Set as a separate flag so the prod test phase can run forwards without
# emailing real customers, then flip ack on later without a redeploy.
_EMAIL_CUSTOMER_ACK_ENABLED = (
    os.environ.get("EMAIL_CUSTOMER_ACK_ENABLED", "false").lower() == "true"
)

# Sender local-parts that mark machine-generated / transactional mail (OTPs,
# shipping/security notifications, third-party system emails). There's no human
# on the other end, so we must NEVER send a customer acknowledgment back to
# them (it's pointless and can bounce or loop). Used as a deterministic backstop
# alongside the classifier's "automated" label — it catches no-reply senders the
# LLM occasionally mislabels as legitimate.
_NO_REPLY_SENDER_TAGS = (
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon", "mailerdaemon", "postmaster", "bounce", "notification",
    "notifications", "alerts", "no.reply", "automated", "auto-confirm", "otp",
)


def _is_no_reply_sender(email: str) -> bool:
    """True when the sender address looks like an automated / no-reply mailbox."""
    local = (email or "").split("@", 1)[0].lower()
    return any(tag in local for tag in _NO_REPLY_SENDER_TAGS)


# Categories we never send a customer acknowledgment for. general_information
# is a pure FYI — the customer isn't waiting on a routed reply, so a "we've
# received your message" ack adds noise without value.
_NO_ACK_CATEGORIES = {"general_information"}


def _resolve_customer_name(sender_name: str, sender_email: str) -> str:
    """Return a name suitable for substituting into '{customer_name}' in an
    acknowledgment template. Falls back through name → email-local-part →
    'there', so we NEVER produce 'Dear [Customer Name],' literally."""
    name = (sender_name or "").strip()
    if name:
        return name
    if sender_email and "@" in sender_email:
        local = sender_email.split("@", 1)[0]
        # Light tidy: a-bc.de → "A-bc De"; keeps it human-ish for greetings.
        cleaned = local.replace(".", " ").replace("_", " ").strip()
        if cleaned:
            return cleaned.title()
    return "there"


def _resolve_acknowledgment_template(category: str) -> Optional[dict]:
    """Look up which template applies to this category via the YAML's
    acknowledgment_template_for map. Returns the {subject, body} dict
    (or None if no template / templates section missing)."""
    rules     = classifier._ROUTING_RULES or {}
    templates = rules.get("templates") or {}
    mapping   = rules.get("acknowledgment_template_for") or {}
    key       = mapping.get(category) or mapping.get("fallback")
    if not key:
        return None
    return templates.get(key)


def _append_ticket_reference(ack_body: str, ticket_number: str) -> str:
    """Append the Zoho ticket number as a footer on the customer complaint
    acknowledgment so they have a reference for any future correspondence.

    Plain-text footer after the signature — standard support-desk style, and
    robust to template wording changes (no fragile signature surgery). Only
    used on the complaint path, and only when a ticket was actually created."""
    return (ack_body.rstrip() +
            f"\n\nYour complaint reference number is #{ticket_number}. "
            f"Please quote this number in any future correspondence about "
            f"this complaint.")


async def _send_complaint_reference_ack(conv_id: int, sender_email: str,
                                        customer_name: str,
                                        ticket_number: Optional[str]) -> None:
    """Send the customer a short follow-up carrying their Zoho ticket number.

    Used when a PAUSED complaint is resolved via the agent decision panel
    (create-new or attach): the customer already got the "forwarded to our
    team" ack at pause time — before any ticket existed — so the number is
    delivered here, once it's known. (The auto-create path embeds the number
    in the first ack instead, so this only covers the dedup-pause case.)

    Gated on EMAIL_CUSTOMER_ACK_ENABLED — the same flag as the primary ack —
    and scoped by the caller to the complaint label only. Best-effort: a send
    failure logs but never breaks the agent's decision."""
    if not _EMAIL_CUSTOMER_ACK_ENABLED or not sender_email or not ticket_number:
        return
    body = (
        f"Dear {customer_name},\n\n"
        f"Thank you for the details. Your complaint has been forwarded to our "
        f"support team and is being tracked under reference number "
        f"#{ticket_number}. Please quote this number in any future "
        f"correspondence about this complaint.\n\n"
        f"Regards,\nTeam Durian"
    )
    try:
        await chatwoot.send_outgoing_message(conv_id, body, to_emails=sender_email)
        print(f"[zoho-dedup] conv {conv_id}: reference #{ticket_number} ack "
              f"sent to {sender_email}")
    except Exception as e:
        print(f"[zoho-dedup] reference ack send failed for conv {conv_id}: {e}")


async def _create_or_pause_zoho_ticket(conv_id: int,
                                       data: dict,
                                       sender_email: str,
                                       escalation_label: str) -> tuple[Optional[str], Optional[dict]]:
    """Dedup-aware Zoho ticket creation. If the contact already has open
    tickets, pause for an agent decision (the ticket-dedup feature); else
    create a fresh ticket and surface it in Chatwoot. Returns
    (zoho_ticket_id, pending_decision). Best-effort — never raises.

    Factored out of handle_message_created so the new 13-category flow can
    request a ticket for complaint / legal_complaint categories without
    duplicating the dedup logic."""
    zoho_ticket = None
    pending_decision = None
    open_tickets = []
    if sender_email:
        try:
            open_tickets = await zoho.search_open_tickets_by_email(sender_email, limit=5)
        except Exception as e:
            print(f"[zoho-dedup] open-ticket lookup failed for {sender_email!r}: {e}")

    if open_tickets or config.ZOHO_TICKET_REQUIRE_APPROVAL:
        why = "approval required" if config.ZOHO_TICKET_REQUIRE_APPROVAL \
            else f"{len(open_tickets)} open ticket(s) for {sender_email!r}"
        print(f"[zoho] conv {conv_id}: pausing auto-create ({why})")
        await _pause_for_agent_decision(
            conv_id=conv_id, sender_email=sender_email,
            escalation_label=escalation_label, candidates=open_tickets,
        )
        pending_decision = {"candidates": [t["id"] for t in open_tickets],
                            "source": escalation_label}
    else:
        try:
            messages, summary = await _ticket_context(conv_id)
            ticket = await zoho.create_ticket(
                data, messages=messages, summary=summary,
                priority=_priority_from_label(escalation_label),
            )
            zoho_ticket = ticket.get("id")
            print(f"[zoho] ticket created for conv {conv_id}: {zoho_ticket}")
            await _surface_ticket_in_chatwoot(conv_id, ticket, source=f"auto_{escalation_label}")
        except Exception as e:
            print(f"[zoho] ERROR creating ticket for conv {conv_id} "
                  f"(reason={escalation_label}): {e}")
    return zoho_ticket, pending_decision


# ── Zoho CRM Contact + Note ──────────────────────────────────────────────
def _crm_note_body(sender_name: str, sender_email: str,
                   subject: str, message_body: str,
                   category_display: str, conv_id: int,
                   ack_sent: bool) -> tuple[str, str]:
    """Build (title, body) for the Note attached to the CRM Contact.

    Sales reads notes in a compact side-panel; keep it human-readable, cite
    the source (Chatwoot), and always link back to the conversation so a
    salesperson can open the full thread with one click."""
    title = f"Enquiry: {(subject or '(no subject)').strip()[:120]}"
    link  = (f"{config.CHATWOOT_PUBLIC_URL.rstrip('/')}"
             f"/app/accounts/{config.CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}")
    lines = [
        f"Source:   Chatwoot ({category_display or 'Uncategorised'})",
        f"From:     {sender_name or sender_email or 'Unknown'} <{sender_email}>",
        f"Subject:  {subject or '(no subject)'}",
        f"Link:     {link}",
        "",
        "--- Customer message ---",
        (message_body or "").strip() or "(empty body)",
    ]
    if ack_sent:
        lines += ["", "--- Auto-acknowledgment sent ---",
                  "The customer has already received our standard "
                  "acknowledgment email."]
    return title, "\n".join(lines)


async def _resolve_crm_owner(message_body: str, subject: str,
                             sender_email: str) -> dict:
    """Auto Contact+Note owner for product / general / existing-order enquiries.

    Phase 1: the retail location matrix is PARKED, so these non-bulk, non-FHC,
    non-doors enquiries are assigned to the central hello@ inbox (the matrix's
    'Product Enquiry — nothing specified' fallback). Kept as a thin wrapper so
    _push_contact_and_note's call site is unchanged; when retail routing is
    turned on for Phase 2 this is where the location matrix comes back."""
    return _fallback_owner()


async def _push_contact_and_note(conv_id: int, sender_name: str,
                                 sender_email: str, subject: str,
                                 message_body: str, category_key: str,
                                 category_display: str,
                                 ack_sent: bool) -> str:
    """Best-effort: find/create the CRM Contact and attach a Note with the
    enquiry summary. Returns an audit line describing what happened.

    Location-gated (client rule): when crm_owner_routing is configured and the
    enquiry's location can't be determined, we do NOT tag CRM at all. When it
    resolves, the Contact is created under that location's owner.

    Idempotent via Chatwoot custom_attributes. Failures NEVER raise — CRM is
    downstream of the ack + forward path and must not block them."""
    if not config.ZOHO_CRM_ENABLED:
        return ""
    if not sender_email:
        return "ℹ️ CRM push skipped — no sender email to key a Contact on."

    # Location → owner. Client rule: no location = no CRM tag at all.
    # Franchise enquiries skip the location matrix — they always belong to
    # the dedicated franchise desk (crm_owner_routing_franchise).
    franchise = (classifier._ROUTING_RULES or {}).get("crm_owner_routing_franchise") or {}
    if category_key == "franchise_dealership" and franchise.get("owner_id"):
        owner = {"configured": True, "location": "franchise",
                 "owner_id":    str(franchise.get("owner_id")),
                 "owner_email": franchise.get("owner_email") or ""}
    else:
        owner = await _resolve_crm_owner(message_body, subject, sender_email)
    if owner.get("configured") and owner.get("location") is None:
        return ("ℹ️ CRM push skipped — location could not be determined "
                "(no CRM owner to route to).")
    owner_id    = owner.get("owner_id", "")
    owner_email = owner.get("owner_email", "")
    location    = owner.get("location") or ""
    owner_line  = (f"\n👤 CRM owner: {location} → {owner_email}"
                   if location else "")

    if config.ZOHO_CRM_DRY_RUN:
        return (f"🔍 CRM dry-run: would upsert Contact for {sender_email} + "
                f"attach a Note.{owner_line}")

    try:
        conv = await chatwoot.get_conversation(conv_id)
    except Exception as e:
        return f"⚠️ CRM push skipped — could not read conversation: {e}"

    custom_attrs = conv.get("custom_attributes") or {}
    existing_id  = custom_attrs.get("crm_contact_id") or ""

    # Contact — reuse existing if we've already pushed this conversation.
    contact_id = existing_id
    created    = False
    if not contact_id:
        try:
            contact_id, created = await zoho_crm.find_or_create_contact(
                sender_email, sender_name, owner_id=owner_id)
        except Exception as e:
            print(f"[crm] find_or_create_contact failed for {sender_email}: {e}")
            return f"⚠️ CRM Contact push failed: {e}"
        if not contact_id:
            return "⚠️ CRM Contact push skipped — no contact id returned."

    # Note — always attach a fresh one so the salesperson sees the LATEST
    # enquiry summary alongside any previous notes.
    title, body = _crm_note_body(sender_name, sender_email, subject,
                                 message_body, category_display, conv_id,
                                 ack_sent)
    try:
        await zoho_crm.create_note("Contacts", contact_id, title, body)
    except Exception as e:
        print(f"[crm] create_note failed for contact {contact_id}: {e}")
        if str(existing_id or "") != str(contact_id):
            await _remember_crm_contact_id(conv_id, contact_id)
        return f"⚠️ CRM Note attach failed (contact {contact_id} created): {e}"

    if str(existing_id or "") != str(contact_id):
        await _remember_crm_contact_id(conv_id, contact_id)
    link = zoho_crm.contact_url(contact_id)
    action = "created" if created else "reused"
    return (f"✅ CRM Contact {action} + note attached — "
            f"[View in Zoho CRM]({link}).{owner_line}")


async def _remember_crm_contact_id(conv_id: int, contact_id: str):
    """Stash crm_contact_id (+ the server-derived deep link) on the
    conversation so re-runs reuse it and the sidebar panel links to the right
    CRM data center — the panel must not guess the UI domain client-side
    (Desk is on .in, Durian's CRM on .com)."""
    try:
        await chatwoot.merge_custom_attributes(
            conv_id, {"crm_contact_id": contact_id,
                      "crm_contact_url": zoho_crm.contact_url(contact_id)})
    except Exception as e:
        print(f"[crm] merge crm_contact_id failed for conv {conv_id}: {e}")


# Ticket references customers quote in their emails: "ticket 253",
# "complaint no. 253", "ref #253", "case id: 253", or a bare "#253".
# Keyword-anchored so order/invoice numbers don't false-positive; the bare-#
# form is filtered against money-ish prefixes below. Every extracted number
# is validated by an actual Zoho lookup, so a stray match costs one API call
# and resolves to nothing — precision here only needs to be "good enough".
_TICKET_REF_KEYWORD_RE = re.compile(
    r"\b(?:ticket|complaint|case|tkt|ref(?:erence)?)\s*"
    r"(?:no\.?|number|id)?\s*[#:]?\s*(?:is|was)?\s*(\d{2,10})\b", re.I)
_TICKET_REF_HASH_RE = re.compile(r"(?<![\w&])#\s?(\d{2,10})\b")
_NOT_TICKET_PREFIXES = re.compile(
    r"\b(?:order|invoice|payment|txn|transaction|awb|tracking|model|item)"
    r"[\s:]*$", re.I)


def _extract_ticket_refs(text: str, cap: int = 3) -> list[str]:
    """Pull candidate Zoho ticket numbers a customer quoted in their message.
    Order-preserving, deduped, capped — the caller validates each against
    Zoho, so this only has to avoid drowning it in obvious non-tickets."""
    refs: list[str] = []
    text = text or ""
    for m in _TICKET_REF_KEYWORD_RE.finditer(text):
        refs.append(m.group(1))
    for m in _TICKET_REF_HASH_RE.finditer(text):
        # A bare "#102215" right after "Order" is an order number, not a
        # ticket — check the few words before the hash.
        if not _NOT_TICKET_PREFIXES.search(text[max(0, m.start() - 16):m.start()]):
            refs.append(m.group(1))
    seen: set[str] = set()
    out = [r for r in refs if not (r in seen or seen.add(r))]
    return out[:cap]


async def _gather_ticket_candidates(sender_email: str, subject: str,
                                    body: str) -> tuple[list[dict], bool]:
    """Collect existing tickets that make auto-creating a new one a duplicate
    risk, from three independent signals:

      1. referenced    — ticket numbers quoted in the message ("#253"),
                         any status: quoting a closed ticket usually means
                         "my issue came back", which the agent must see.
                         Checked FIRST so it wins the label and ordering — a
                         customer who names a ticket is talking about THAT
                         ticket, so it must not hide behind a "same contact"
                         tag or fall off the end of the cap.
      2. same contact  — open tickets under the sender's email
      3. similar content — open tickets whose description matches the
                         message keywords (catches the same complaint
                         re-sent from a DIFFERENT email address)

    Each candidate carries a `match` reason so the agent panel / private note
    can say WHY it surfaced. Deduped by id, referenced-first, capped at 5.

    Returns (candidates, referenced_present). `referenced_present=True` means
    the customer named a real existing ticket → the caller offers attach-only
    (creating a brand-new duplicate is never right when they've pointed at an
    existing ticket)."""
    candidates: list[dict] = []
    seen: set[str] = set()

    def _add(tickets: list[dict], reason: str) -> None:
        for t in tickets or []:
            tid = str(t.get("id"))
            if tid in seen:
                continue
            seen.add(tid)
            candidates.append({**t, "match": reason})

    referenced_present = False
    for ref in _extract_ticket_refs(f"{subject}\n{body}"):
        try:
            t = await zoho.get_ticket_by_number(ref)
        except Exception as e:
            print(f"[zoho-dedup] ref #{ref} lookup failed: {e}")
            continue
        if t:
            referenced_present = True
            _add([t], f"#{ref} referenced in message")
        else:
            print(f"[zoho-dedup] message mentions #{ref} but no such ticket — ignoring")

    if sender_email:
        try:
            _add(await zoho.search_open_tickets_by_email(sender_email, limit=5),
                 "same contact")
        except Exception as e:
            print(f"[zoho-dedup] open-ticket lookup failed for {sender_email!r}: {e}")

    try:
        _add(await zoho.search_tickets_by_content(subject, body, limit=3),
             "similar content")
    except Exception as e:
        print(f"[zoho-dedup] content search failed: {e}")

    return candidates[:5], referenced_present


async def _maybe_create_complaint_ticket(conv_id: int, sender_name: str,
                                         sender_email: str,
                                         subject: str = "",
                                         body: str = "") -> tuple[list[str], bool, Optional[dict]]:
    """For product `complaint` emails: auto-create a Zoho Desk ticket assigned
    to the client's customersupport agent — in ADDITION to the email forward.

    Fully automatic: bypasses ZOHO_TICKET_REQUIRE_APPROVAL (this is a
    dedicated, client-requested auto path, not the escalation flow). Assigned to
    COMPLAINT_TICKET_OWNER_DESK_ID (empty → unassigned). Best-effort — any
    failure logs and returns an audit line; it never blocks the forward.

    Dedup: pause for the agent decision instead of auto-creating whenever
    _gather_ticket_candidates finds a duplicate risk — the sender's own open
    tickets, a ticket number quoted in the message (even from a different
    email address), or an open ticket with matching content (same complaint
    re-sent from another address). The approval-gate bypass above only
    applies when there is nothing to duplicate.

    Returns (audit_lines, paused, ticket): `paused=True` means a decision is
    pending and the caller must NOT auto-resolve the conversation — a resolved
    conv drops out of the open queue, and the pending decision would sit unseen
    until the customer complains a third time. `ticket` is the created Zoho
    ticket dict when one was auto-created (None on the paused / failed / disabled
    paths) so the caller can put its number in the customer acknowledgment."""
    if not config.COMPLAINT_AUTO_TICKET_ENABLED:
        return [], False, None
    candidates, referenced = await _gather_ticket_candidates(
        sender_email, subject, body)
    if candidates:
        reasons = ", ".join(sorted({c.get("match") or "?" for c in candidates}))
        print(f"[zoho] conv {conv_id}: pausing complaint auto-create "
              f"({len(candidates)} candidate(s): {reasons}"
              f"{'; attach-only' if referenced else ''})")
        await _pause_for_agent_decision(
            conv_id=conv_id, sender_email=sender_email,
            escalation_label="complaint", candidates=candidates,
            attach_only=referenced,
        )
        # When the customer named an existing ticket, this is a follow-up on
        # THAT ticket — attach, don't create a duplicate. Otherwise it's a
        # possible-duplicate judgement call (attach vs create new).
        line = ("⏸️ Complaint references an existing Zoho ticket — attach this "
                "conversation to it (no new ticket needed); agent decision needed."
                if referenced else
                "⏸️ Complaint ticket paused — possibly duplicate of existing "
                "Zoho ticket(s); agent decision needed (attach vs create new).")
        return [line], True, None
    try:
        assignee_id = config.COMPLAINT_TICKET_OWNER_DESK_ID or None
        messages, summary = await _ticket_context(conv_id)
        # Structured slots gathered by the complaint-details gate (order id /
        # phone / reason) → surfaced at the top of the ticket for support.
        complaint_details = None
        try:
            conv_now = await chatwoot.get_conversation(conv_id)
            complaint_details = (conv_now.get("custom_attributes") or {}).get("complaint_details") or None
        except Exception:
            pass
        payload = {"conversation": {
            "id": conv_id,
            "meta": {"sender": {"name": sender_name, "email": sender_email}},
        }}
        ticket = await zoho.create_ticket(
            payload, messages=messages, summary=summary,
            assignee_id=assignee_id, complaint_details=complaint_details)
        ticket_id = ticket.get("id")
        print(f"[zoho] complaint ticket created for conv {conv_id}: "
              f"{ticket_id} assignee={assignee_id or '(none)'}")
        await _surface_ticket_in_chatwoot(conv_id, ticket, source="auto_complaint")
        who = f" → assigned to agent {assignee_id}" if assignee_id \
            else " (unassigned — no owner configured)"
        return [f"🎫 Zoho Desk complaint ticket created{who}."], False, ticket
    except Exception as e:
        print(f"[zoho] complaint ticket failed for conv {conv_id}: {e}")
        return [f"⚠️ Complaint ticket could not be created: {e}"], False, None


# ── BMS order lookup (existing_order_enquiry flow) ─────────────────────────
# When a customer emails about an existing order, the bridge tries to fetch
# their order(s) from BMS and DRAFTS everything for the agent — the order
# snapshot + a suggested customer reply land in a private note, and a label
# marks the conversation ready. NOTHING is auto-sent (testing phase; also,
# BMS returns orders for whatever phone you query — see bms.py — so a human
# must always verify the match before replying).
#
# Identifier priority (client's flow): order id in the email → lookup by id;
# else phone (email text, then the Chatwoot contact) → lookup by phone; else
# draft a "please share your order no. / phone / purchase location" ask and
# set `pending_order_lookup` so the customer's reply re-enters this flow
# (handle_message_created checks the attribute BEFORE the already-classified
# guard). Capped at ORDER_LOOKUP_MAX_ASKS so it can never nag forever.

ORDER_REPLY_READY_LABEL   = "order-reply-ready"
ORDER_DETAILS_NEEDED_LABEL = "order-details-needed"

# Order ids are 4-8 digits, quoted as "D#75833", "#75833" or keyword-anchored
# ("order no. 75833"). The 8-digit cap plus the phone pattern below keeps a
# 10-digit mobile number from being read as an order id and vice versa.
_ORDER_ID_KEYWORD_RE = re.compile(
    r"\b(?:order|ord)\s*(?:id|no\.?|number)?\s*[#:]?\s*(?:is|was)?\s*"
    r"D?#?\s*(\d{4,8})\b", re.I)
_ORDER_ID_DHASH_RE = re.compile(r"\bD#\s*(\d{4,8})\b", re.I)
# Indian mobile: optional +91/91/0 prefix, 10 digits starting 6-9, separators
# tolerated between digits ("70630 65631", "70630-65631").
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?([6-9](?:[\s-]?\d){9})(?!\d)")


def _extract_order_ids(text: str, cap: int = 3) -> list[str]:
    refs: list[str] = []
    for rx in (_ORDER_ID_DHASH_RE, _ORDER_ID_KEYWORD_RE):
        refs += [m.group(1) for m in rx.finditer(text or "")]
    seen: set[str] = set()
    return [r for r in refs if not (r in seen or seen.add(r))][:cap]


def _extract_phones(text: str, cap: int = 3) -> list[str]:
    phones: list[str] = []
    for m in _PHONE_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 10:
            phones.append(digits)
    seen: set[str] = set()
    return [p for p in phones if not (p in seen or seen.add(p))][:cap]


def _format_order_snapshot(orders: list[dict]) -> str:
    """Markdown block for the private note: what BMS knows, agent-readable."""
    lines = []
    for o in orders:
        head = f"**{o.get('order_number') or o.get('order_id')}** — {o.get('status') or '?'}"
        if o.get("created_date"):
            head += f" · placed {o['created_date']}"
        if o.get("payment_method"):
            head += f" · {o['payment_method']}"
        lines.append(f"- {head}")
        for it in o.get("items") or []:
            qty = f" × {it['quantity']}" if it.get("quantity") else ""
            lines.append(f"  - {it.get('name') or '?'} ({it.get('sku')}){qty}")
        place = ", ".join(x for x in (o.get("delivery_city"), o.get("delivery_state")) if x)
        money = f"₹{o['gross_amount']}" if o.get("gross_amount") else ""
        if o.get("paid_amount"):
            money += f" (paid ₹{o['paid_amount']})"
        tail = " · ".join(x for x in (f"deliver to {place}" if place else "",
                                      money,
                                      f"delivery {o['delivery_date']}" if o.get("delivery_date") else "",
                                      f"under {o['customer_name']}" if o.get("customer_name") else ""
                                      ) if x)
        if tail:
            lines.append(f"  - {tail}")
    return "\n".join(lines)


# Single "ask for details" email — used whenever we do NOT yet have a verified
# order id + phone (one/both missing, or the two didn't match an order). It
# carries NO order data, so it is safe to auto-send. {needed} lists exactly
# what's still required (client rule: BOTH the order number AND the registered
# phone are mandatory before any order detail is shared — a safety measure).
_ASK_DETAILS_TEMPLATE = """Dear {customer_name},

Thank you for reaching out to Durian.

For your security, we can look up your order only once we have BOTH of the following:
{needed}

Kindly reply with these details and we'll share your order status right away.

Regards,
Team Durian"""

_ORDER_NO_LINE = "Your order number (it looks like D#12345)"
_PHONE_LINE    = "The phone number used at the time of purchase"


def _needed_details(have_id: bool, have_phone: bool) -> str:
    """Bulleted list of the still-required identifiers for the ask email. When
    both were supplied but couldn't be verified, we ask them to re-check both."""
    items = []
    if not have_id:
        items.append(_ORDER_NO_LINE)
    if not have_phone:
        items.append(_PHONE_LINE)
    if not items:                      # both given but unverified → re-check both
        items = [_ORDER_NO_LINE, _PHONE_LINE]
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))


def _phones_match(provided: str, order_phone: str) -> bool:
    """True when two phone numbers match on their last 10 digits (ignoring
    +91 / spaces / dashes). Empty on either side → False."""
    a = re.sub(r"\D", "", provided or "")[-10:]
    b = re.sub(r"\D", "", order_phone or "")[-10:]
    return len(a) == 10 and a == b


async def _draft_order_reply(customer_name: str, question: str,
                             orders: list[dict], matched_by: str) -> str:
    """LLM-drafted customer reply, hard-grounded on the BMS data. Falls back
    to a deterministic snapshot reply if the LLM call fails — the agent must
    always end up with SOMETHING sendable in the note."""
    from llm_client import client
    schema = {
        "name": "order_reply_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["reply"],
            "properties": {"reply": {"type": "string"}},
        },
    }
    system = (
        "You draft warm, professional customer-support email replies for "
        "Durian, an Indian furniture brand. Ground EVERY fact strictly in the "
        "ORDER DATA JSON — never invent statuses, dates, amounts or products.\n"
        "\n"
        "Structure the reply:\n"
        "1. Open by directly answering the customer's specific question using "
        "the order facts (e.g. if they ask about delivery, lead with the "
        "delivery status/date).\n"
        "2. Then give a clear, complete summary of the order so they have the "
        "full picture, including EVERY field that is present in the data: the "
        "order number, current status, the product(s) ordered (name and "
        "quantity), the order/purchase date, the payment method, the amount, "
        "the expected delivery date, and the delivery city/address. Present "
        "this as a tidy labelled list (e.g. 'Order Number: …', 'Status: …') so "
        "it's easy to read.\n"
        "3. Omit any field that is empty/missing — never write 'N/A' or a blank "
        "value, and never guess. If a field the customer asked about is "
        "missing, say the team will confirm it shortly.\n"
        "4. Format money with a ₹ sign and dates in a readable form.\n"
        "\n"
        f"Address the customer as '{customer_name}'. Plain text (no markdown "
        "symbols like * or #), sign off exactly:\nRegards,\nTeam Durian"
    )
    user = (
        f"CUSTOMER'S MESSAGE:\n{question[:2000]}\n\n"
        f"ORDER DATA (matched by {matched_by}):\n"
        f"{json.dumps(orders, ensure_ascii=False)}"
    )
    try:
        resp = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        reply = (json.loads(resp.choices[0].message.content) or {}).get("reply", "")
        if reply.strip():
            return reply.strip()
    except Exception as e:
        print(f"[order-lookup] reply drafting failed: {e}")
    # Deterministic fallback: greeting + facts, no LLM required.
    facts = _format_order_snapshot(orders).replace("**", "")
    return (f"Dear {customer_name},\n\nThank you for reaching out. Here are "
            f"the details of your order(s):\n\n{facts}\n\nPlease let us know "
            f"if we can help with anything else.\n\nRegards,\nTeam Durian")


async def _label_conversation(conv_id: int, label: str) -> None:
    """ensure + add a label, best-effort (a labelling failure must never
    break the lookup flow — the private note is the real deliverable)."""
    try:
        await chatwoot.ensure_label(label)
        await chatwoot.add_label(conv_id, label)
    except Exception as e:
        print(f"[order-lookup] labelling {conv_id} '{label}' failed: {e}")


# Umbrella marker for the unified "Agent needs" sidebar section: applied at
# EVERY point where a human must decide/act — a gate hit its ask cap with details
# still missing, a review or social DM was handed off, an email needs a category
# decision, a Zoho ticket is paused for approval, or a deal-vertical lead is
# waiting on Create Deal. The per-channel variant (agent-needed-email / -instagram
# / …) backs the in-view channel dropdown. Category emails ALSO keep `needs-review`
# (their own dedicated view) — this umbrella is what makes them show in the
# unified section too. Cleared once the human acts (deal/ticket created, category
# resolved) or the conversation resolves — see _clear_agent_needed.
AGENT_NEEDED_LABEL = "agent-needed"
# Kept in one place so the flaggers and the cleanup can't drift.
_AGENT_NEEDED_ALL = [AGENT_NEEDED_LABEL] + [
    f"{AGENT_NEEDED_LABEL}-{c}" for c in ("email", "instagram", "facebook", "whatsapp", "reviews")]


async def _flag_agent_needed(conv_id: int, channel: str) -> None:
    """Mark a conversation as awaiting a human: the shared agent-needed umbrella
    plus a per-channel label (agent-needed-email / -instagram / …). Best-effort."""
    for lbl in (AGENT_NEEDED_LABEL, f"{AGENT_NEEDED_LABEL}-{channel}"):
        await _label_conversation(conv_id, lbl)


async def _clear_agent_needed(conv_id: int, conv: Optional[dict] = None) -> list[str]:
    """Remove the agent-needed umbrella + per-channel labels once a human has
    acted (deal/ticket created, category resolved) or the conversation resolved.
    When `conv` actually carries a `labels` list we only touch labels present, so
    the frequent auto-resolves stay cheap. When it's absent (None) — e.g. a fetch
    that doesn't include labels — we try every marker (remove_label no-ops on the
    ones not set, so this is safe, just a few extra GETs). Best-effort."""
    labels = conv.get("labels") if conv is not None else None
    targets = ([l for l in labels if l in _AGENT_NEEDED_ALL]
               if labels is not None else _AGENT_NEEDED_ALL)
    removed: list[str] = []
    for lbl in targets:
        try:
            await chatwoot.remove_label(conv_id, lbl)
            removed.append(lbl)
        except Exception:
            pass
    return removed


async def _post_order_reply_card(conv_id: int, draft: str, context: str = "") -> None:
    """Post the drafted customer reply as an interactive 'Send to customer'
    card (content_attributes.type == 'ai_order_reply'), NOT a plain private
    note. The agent reviews/edits and sends it in one click — nothing is sent
    automatically. `context` is an agent-only snapshot (order details / match
    reason) rendered read-only on the card; it is never sent to the customer.
    Best-effort — falls back to a private note if card creation fails."""
    ca = {"type": "ai_order_reply", "suggestion": draft}
    if context:
        ca["context"] = context
    try:
        await chatwoot.create_message(conv_id, draft, message_type="outgoing",
                                      private=True, content_attributes=ca)
    except Exception as e:
        print(f"[order-lookup] card post failed for conv {conv_id}: {e}")
        note = (context + "\n\n" if context else "") + \
            f"✉️ Drafted reply (card unavailable):\n\n{draft}"
        await chatwoot.post_private_note(conv_id, note)


async def _run_order_lookup(conv_id: int, sender_name: str, sender_email: str,
                            subject: str, body: str, *, attempt: int) -> list[str]:
    """One pass of the order-lookup flow. Client's safety rule: reveal order
    details ONLY when the customer supplied BOTH an order number AND the phone,
    and the phone matches that order's registered number. Otherwise auto-send a
    single ask for the still-missing/unverified detail(s). `attempt` = asks
    already sent (0 on first pass; from pending_order_lookup on re-entry)."""
    text = f"{subject}\n{body}"
    order_ids = _extract_order_ids(text)
    phones    = _extract_phones(text)

    # Order id may also come from an attached invoice/screenshot (still the
    # customer's own document). The PHONE must be typed by the customer — for
    # the safety gate we deliberately DON'T auto-fill it from the stored contact.
    conv_data = await chatwoot.get_conversation(conv_id)
    attrs = conv_data.get("custom_attributes") or {}
    if not order_ids:
        for d in attrs.get("extracted_documents") or []:
            oid = str((d or {}).get("order_id") or "").strip().lstrip("D#")
            if oid.isdigit() and 4 <= len(oid) <= 8:
                order_ids.append(oid)
                break

    name = _resolve_customer_name(sender_name, sender_email)
    have_id, have_phone = bool(order_ids), bool(phones)

    # ── Helper: ask for the missing/unverified details ────────────────────
    # Carries NO order data → safe to auto-send. Order DETAILS (PII) are always
    # an agent card; only this ask is auto-sent (when ORDER_LOOKUP_AUTO_SEND).
    async def _ask_for_details(reason: str) -> list[str]:
        if attempt >= config.ORDER_LOOKUP_MAX_ASKS:
            await chatwoot.merge_custom_attributes(conv_id, {"pending_order_lookup": None})
            await _flag_agent_needed(conv_id, "email")
            print(f"[order-lookup] conv {conv_id}: ask cap reached ({attempt}) — leaving to agent")
            return [f"📦 Order lookup: {reason}; ask cap ({attempt}) reached — left to the team."]
        draft = _ASK_DETAILS_TEMPLATE.format(
            customer_name=name, needed=_needed_details(have_id, have_phone))
        ctx = f"📦 Order lookup: {reason} — requesting the required details from the customer."
        await chatwoot.merge_custom_attributes(conv_id, {
            "pending_order_lookup": {"attempts": attempt + 1, "asked_at": _now_iso(),
                                     "category": "existing_order_enquiry"}})
        if config.ORDER_LOOKUP_AUTO_SEND:
            await chatwoot.send_outgoing_message(conv_id, draft)
            await chatwoot.post_private_note(conv_id, f"📦 **Auto-sent** request for order details. {ctx}")
            await _label_conversation(conv_id, ORDER_DETAILS_NEEDED_LABEL)
            print(f"[order-lookup] conv {conv_id}: auto-sent details ask ({reason})")
            return [f"📦 Order lookup: {reason}; auto-sent request for the required details."]
        await _post_order_reply_card(conv_id, draft, context=ctx)
        await _label_conversation(conv_id, ORDER_DETAILS_NEEDED_LABEL)
        print(f"[order-lookup] conv {conv_id}: details ask drafted ({reason})")
        return [f"📦 Order lookup: {reason}; request-for-details reply drafted for agent review."]

    # ── Both are mandatory. Missing one/both → ask, no lookup. ────────────
    if not (have_id and have_phone):
        missing = ("no order number or phone" if not (have_id or have_phone)
                   else "phone missing" if have_id else "order number missing")
        return await _ask_for_details(missing)

    # ── Both supplied → look up by order id, then VERIFY the phone belongs
    # to that order (safety: both, AND they must match the same order). ────
    order, matched_id = None, None
    for oid in order_ids:
        o = await bms.get_order_by_id(oid)
        if o:
            order, matched_id = o, oid
            break

    if not order or not any(_phones_match(ph, order.get("customer_phone")) for ph in phones):
        # Not found OR the phone doesn't match. Reveal NOTHING (don't even
        # confirm the order exists) — just ask them to re-check both.
        return await _ask_for_details("order number + phone could not be verified")

    # ── Verified → draft the order details as an agent card (never auto-sent).
    await chatwoot.merge_custom_attributes(conv_id, {"pending_order_lookup": None})
    try:
        await chatwoot.remove_label(conv_id, ORDER_DETAILS_NEEDED_LABEL)
    except Exception:
        pass
    draft = await _draft_order_reply(name, body, [order], f"order {matched_id} + phone verified")
    context = (f"📦 BMS order lookup — order {matched_id}, phone verified.\n\n"
               f"{_format_order_snapshot([order])}")
    await _post_order_reply_card(conv_id, draft, context)
    await _label_conversation(conv_id, ORDER_REPLY_READY_LABEL)
    print(f"[order-lookup] conv {conv_id}: verified order {matched_id}; details card drafted")
    return [f"📦 BMS lookup: order {matched_id} verified by phone; "
            f"details drafted as a send card for agent review."]


async def _handle_order_lookup_reply(conv_id: int, data: dict,
                                     conv: dict, pending: dict) -> dict:
    """Customer replied on a conversation awaiting order details. Runs the
    lookup pass over the NEW message (plus the usual extra sources) and
    returns the webhook response. Called before the already-classified guard
    in handle_message_created."""
    content = data.get("content") or ""
    subject = ((data.get("content_attributes") or {}).get("email") or {}).get("subject") or ""
    sender  = (conv.get("meta") or {}).get("sender") or {}
    audit = await _run_order_lookup(
        conv_id,
        sender.get("name") or "",
        sender.get("email") or "",
        subject, content,
        attempt=int(pending.get("attempts") or 1),
    )
    print(f"[order-lookup] conv {conv_id}: reply re-entry → {audit}")
    return {"handled": "order_lookup_reply", "audit": audit}


# ── Deal-details gate (bulk order / FHC / door) ────────────────────────────
# Sales categories that create a CRM deal and therefore require the customer's
# phone + city up front (client rule). Franchise is intentionally excluded —
# it routes to the dedicated franchise desk, not a location-owner deal.
_DEAL_DETAILS_CATEGORIES = {
    "project_bulk_order", "full_home_customization", "doors_veneer_plywood",
}
DEAL_DETAILS_NEEDED_LABEL = "deal-details-needed"
DEAL_READY_LABEL          = "deal-ready"
# Applied when a CRM Deal is actually created, so agents can filter every
# conversation that produced a deal — plus a per-vertical tag for what kind.
DEAL_CREATED_LABEL        = "deal-created"
_DEAL_VERTICAL_LABEL = {
    "project_bulk_order":      "deal-bulk",
    "full_home_customization": "deal-fhc",
    "doors_veneer_plywood":    "deal-doors",
    "product_enquiry":         "deal-product",
    "franchise_dealership":    "deal-franchise",
}

# Fallback ask when the LLM draft is unavailable (keeps the flow moving).
_DEAL_DETAILS_FALLBACK_ASK = """Dear {customer_name},

Thank you for your interest in Durian.

To register your enquiry and connect you with the right team, could you please share:
1. The phone number we can reach you on
2. Your city

We'll take it forward as soon as we receive these.

Regards,
Team Durian"""

# Sent (auto) once BOTH phone + city are in hand — the enquiry is registered and
# the agent can create the deal. Kept simple/fixed (the "not template" rule was
# for the ASK; this confirmation isn't asking for anything).
_DEAL_DETAILS_ACK = """Dear {customer_name},

Thank you — we've registered your enquiry and shared it with our team. Our representative will get in touch with you shortly to take it forward.

Regards,
Team Durian"""


async def _deal_details_gate_llm(customer_name: str, text: str,
                                 have_phone: bool) -> dict:
    """One LLM call: extract the city the customer EXPLICITLY stated (empty if
    none), and — when phone and/or city is still missing — draft a warm reply
    asking for the missing detail(s) plus their requirement. Returns
    {"city": str, "ask_reply": str}; ask_reply is "" when nothing is missing.
    Best-effort — returns empties on any failure so the flow never breaks."""
    from llm_client import client
    schema = {
        "name": "deal_details_gate", "strict": True,
        "schema": {"type": "object", "additionalProperties": False,
                   "required": ["city", "ask_reply"],
                   "properties": {"city": {"type": "string"},
                                  "ask_reply": {"type": "string"}}},
    }
    system = (
        "You are a warm, professional customer-support agent for Durian, an "
        "Indian furniture brand, handling a bulk-order / full-home-customisation "
        "/ door enquiry.\n"
        "Return two fields:\n"
        "1. city — the city or town the customer EXPLICITLY mentions as theirs "
        "(delivery/location/where they are). Empty string if they did not "
        "clearly state a city. Never guess from area codes or names.\n"
        "2. ask_reply — to register the enquiry we REQUIRE the customer's phone "
        "number AND city. 'Already have phone' below tells you if the phone is "
        "present. If the phone is missing OR the city is empty, write a short, "
        "warm reply that: thanks them, says we'd love to help with their "
        "requirement, and asks ONLY for what's still missing (phone and/or "
        "city). Do not ask for quantity, timeline or budget. "
        f"Address them as '{customer_name}', plain text, sign off exactly:\n"
        "Regards,\nTeam Durian\n"
        "If BOTH phone and city are already present, return ask_reply as an "
        "empty string."
    )
    user = f"Already have phone: {have_phone}\n\nCUSTOMER ENQUIRY:\n{text[:2000]}"
    try:
        resp = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        parsed = json.loads(resp.choices[0].message.content) or {}
        return {"city": (parsed.get("city") or "").strip(),
                "ask_reply": (parsed.get("ask_reply") or "").strip()}
    except Exception as e:
        print(f"[deal-gate] LLM gate failed: {e}")
        return {"city": "", "ask_reply": ""}


async def _run_deal_details_gate(conv_id: int, sender_name: str, sender_email: str,
                                 subject: str, body: str, category: str,
                                 *, attempt: int) -> list[str]:
    """One pass of the deal-details gate. Captures phone + city; when both are
    present it registers the enquiry (auto-acknowledges, marks deal-ready) and
    when either is missing it auto-sends ONE AI-drafted ask (replacing the
    generic acknowledgment). `attempt` = asks already sent."""
    text = f"{subject}\n{body}"
    phones = _extract_phones(text)
    have_phone = bool(phones)
    name = _resolve_customer_name(sender_name, sender_email)

    gate = await _deal_details_gate_llm(name, text, have_phone)
    city = gate.get("city") or ""
    have_city = bool(city)

    if have_phone and have_city:
        # Both in hand → capture for the deal, acknowledge, mark ready.
        await chatwoot.merge_custom_attributes(conv_id, {
            "deal_customer_details": {"phone": phones[0], "city": city,
                                      "captured_at": _now_iso()},
            "pending_deal_details": None})
        try:
            await chatwoot.remove_label(conv_id, DEAL_DETAILS_NEEDED_LABEL)
        except Exception:
            pass
        if _EMAIL_CUSTOMER_ACK_ENABLED and sender_email:
            try:
                await chatwoot.send_outgoing_message(
                    conv_id, _DEAL_DETAILS_ACK.format(customer_name=name),
                    to_emails=sender_email)
            except Exception as e:
                print(f"[deal-gate] ack send failed for conv {conv_id}: {e}")
        await _label_conversation(conv_id, DEAL_READY_LABEL)
        print(f"[deal-gate] conv {conv_id}: captured phone + city ({city}) — deal-ready")
        return [f"📝 Deal details captured (phone + {city}); enquiry acknowledged "
                f"— ready for the agent to create the deal."]

    # Missing phone and/or city → auto-send the AI ask (no PII), capped.
    missing = ", ".join(x for x in ("phone" if not have_phone else "",
                                    "city" if not have_city else "") if x)
    if attempt >= config.DEAL_DETAILS_MAX_ASKS:
        await chatwoot.merge_custom_attributes(conv_id, {"pending_deal_details": None})
        await _flag_agent_needed(conv_id, "email")
        print(f"[deal-gate] conv {conv_id}: ask cap ({attempt}) — leaving to agent")
        return [f"📝 Deal details ({missing}) still missing after {attempt} ask(s) — left to the team."]
    ask = gate.get("ask_reply") or _DEAL_DETAILS_FALLBACK_ASK.format(customer_name=name)
    await chatwoot.merge_custom_attributes(conv_id, {
        "pending_deal_details": {"attempts": attempt + 1, "asked_at": _now_iso(),
                                 "category": category}})
    if sender_email:
        try:
            await chatwoot.send_outgoing_message(conv_id, ask, to_emails=sender_email)
        except Exception as e:
            print(f"[deal-gate] ask send failed for conv {conv_id}: {e}")
    await _label_conversation(conv_id, DEAL_DETAILS_NEEDED_LABEL)
    print(f"[deal-gate] conv {conv_id}: auto-sent AI ask for {missing} (attempt {attempt + 1})")
    return [f"📝 Deal details ({missing}) missing — auto-sent request for the required details."]


async def _handle_deal_details_reply(conv_id: int, data: dict,
                                     conv: dict, pending: dict) -> dict:
    """Customer replied on a conversation awaiting deal details. Re-runs the
    gate over the NEW message. Called before the already-classified guard."""
    content = data.get("content") or ""
    subject = ((data.get("content_attributes") or {}).get("email") or {}).get("subject") or ""
    sender  = (conv.get("meta") or {}).get("sender") or {}
    audit = await _run_deal_details_gate(
        conv_id, sender.get("name") or "", sender.get("email") or "",
        subject, content, str(pending.get("category") or ""),
        attempt=int(pending.get("attempts") or 1))
    print(f"[deal-gate] conv {conv_id}: reply re-entry → {audit}")
    return {"handled": "deal_details_reply", "audit": audit}


# ── Retail routing gate (product enquiry → city → showroom → owner) ─────────
# Retail furniture purchase enquiries (a few pieces, not bulk) route to a
# showroom's CRM owner. The gate: ask the city if missing → if the city has one
# showroom, capture its owner; if several, list them and ask which is nearest →
# on the pick, capture that showroom's owner for the agent's Create Deal. City
# not in the directory → customer support.
RETAIL_NEEDED_LABEL = "retail-details-needed"

_RETAIL_CITY_FALLBACK_ASK = """Dear {customer_name},

Thank you for your interest in Durian furniture!

To point you to the nearest showroom and assist you better, could you please let us know which city you're in?

Regards,
Team Durian"""


def _retail_showroom_ask(name: str, city_data: dict) -> str:
    return (f"Dear {name},\n\nThank you for enquiring about our products! "
            f"We have the following Durian showrooms in "
            f"{city_data.get('display', 'your city')}:\n\n"
            f"{retail.list_showrooms_text(city_data)}\n\n"
            "Could you let us know which location is most convenient for you "
            "(the one nearest to you), so our showroom team can assist you "
            "further?\n\nRegards,\nTeam Durian")


async def _retail_gate_llm(customer_name: str, text: str, *, stage: str,
                           showroom_list: str = "") -> dict:
    """One LLM call. stage='city' → extract the customer's city + draft an ask
    if missing. stage='showroom' → pick which listed showroom they chose (1-based
    `choice`, 0 if unclear) + draft a re-ask. Returns {city, choice, ask_reply}."""
    from llm_client import client
    schema = {"name": "retail_gate", "strict": True,
              "schema": {"type": "object", "additionalProperties": False,
                         "required": ["city", "choice", "ask_reply"],
                         "properties": {"city": {"type": "string"},
                                        "choice": {"type": "integer"},
                                        "ask_reply": {"type": "string"}}}}
    if stage == "showroom":
        system = (
            "You are a warm Durian furniture support agent. The customer is "
            "choosing which showroom to visit from this numbered list:\n"
            f"{showroom_list}\n\n"
            "Set choice = the 1-based number they picked — match by number, "
            "locality, or name (e.g. 'JP Nagar', 'Marathahalli', 'the second "
            "one'). Set choice=0 ONLY if you truly cannot tell which one. Leave "
            "city empty. If choice=0, write a short warm ask_reply re-listing the "
            "options and asking them to pick the nearest; otherwise ask_reply "
            f"empty. Address them as '{customer_name}', sign off exactly:\n"
            "Regards,\nTeam Durian")
        user = f"CUSTOMER REPLY:\n{text[:1500]}"
    else:
        system = (
            "You are a warm Durian furniture support agent handling a RETAIL "
            "product purchase enquiry (a few pieces of furniture). Set city = the "
            "Indian city the customer is in / wants to buy in / wants the nearest "
            "showroom for, if they mention one; else empty. Always set choice=0. "
            "If city is empty, write a short warm ask_reply thanking them for "
            "their interest and asking which city they are in so we can share the "
            f"nearest Durian showroom. Address them as '{customer_name}', sign off "
            "exactly:\nRegards,\nTeam Durian\nIf city is present, ask_reply empty.")
        user = f"ENQUIRY:\n{text[:1500]}"
    try:
        resp = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": schema})
        parsed = json.loads(resp.choices[0].message.content) or {}
        return {"city": (parsed.get("city") or "").strip(),
                "choice": int(parsed.get("choice") or 0),
                "ask_reply": (parsed.get("ask_reply") or "").strip()}
    except Exception as e:
        print(f"[retail-gate] LLM gate failed: {e}")
        return {"city": "", "choice": 0, "ask_reply": ""}


async def _retail_ask(conv_id: int, sender_email: str, ask: str,
                      pending_extra: dict, attempt: int, what: str) -> list[str]:
    if attempt >= config.RETAIL_DETAILS_MAX_ASKS:
        await chatwoot.merge_custom_attributes(conv_id, {"pending_retail": None})
        await _flag_agent_needed(conv_id, "email")
        return [f"🛍️ Retail: {what} still missing after {attempt} ask(s) — left to the team."]
    pending = {"attempts": attempt + 1, "asked_at": _now_iso(), **pending_extra}
    await chatwoot.merge_custom_attributes(conv_id, {"pending_retail": pending})
    if sender_email:
        try:
            await chatwoot.send_outgoing_message(conv_id, ask, to_emails=sender_email)
        except Exception as e:
            print(f"[retail-gate] ask send failed for conv {conv_id}: {e}")
    await _label_conversation(conv_id, RETAIL_NEEDED_LABEL)
    return [f"🛍️ Retail: auto-asked customer for {what} (attempt {attempt + 1})."]


async def _retail_capture_owner(conv_id: int, sender_email: str, name: str,
                                city_key: str, city_data: dict,
                                showroom: dict) -> list[str]:
    """A showroom is settled → stash its CRM owner for the agent's Create Deal,
    confirm to the customer, and post the agent note."""
    owner = {"owner_id": str(showroom.get("owner_id") or ""),
             "owner_name": showroom.get("owner_name") or "",
             "crm_email": showroom.get("crm_email") or "",
             "location": showroom.get("location") or "",
             "city": city_data.get("display", city_key)}
    await chatwoot.merge_custom_attributes(
        conv_id, {"retail_deal_owner": owner, "pending_retail": None})
    try:
        await chatwoot.remove_label(conv_id, RETAIL_NEEDED_LABEL)
    except Exception:
        pass
    await _label_conversation(conv_id, "retail-routed")
    if _EMAIL_CUSTOMER_ACK_ENABLED and sender_email:
        try:
            await chatwoot.send_outgoing_message(
                conv_id,
                f"Dear {name},\n\nThank you! Our {owner['location']} showroom team "
                "will assist you with your purchase and reach out to you shortly.\n\n"
                "Regards,\nTeam Durian", to_emails=sender_email)
        except Exception as e:
            print(f"[retail-gate] confirm send failed for conv {conv_id}: {e}")
    try:
        await chatwoot.post_private_note(
            conv_id,
            f"🛍️ **Retail showroom selected — {owner['location']}**\n\n"
            f"CRM owner: {owner['owner_name'] or owner['crm_email']} "
            f"(id {owner['owner_id']}).\n\n"
            "→ Click **Create Deal** to log this enquiry to that showroom owner.")
    except Exception as e:
        print(f"[retail-gate] note failed for conv {conv_id}: {e}")
    return [f"🛍️ Retail routed to {owner['location']} (owner {owner['owner_id']}) — Create Deal ready."]


async def _retail_route_to_support(conv_id: int, city_name: str) -> list[str]:
    """City not in the showroom directory → hand to customer support."""
    await chatwoot.merge_custom_attributes(conv_id, {"pending_retail": None})
    await _label_conversation(conv_id, "retail-support")
    await _flag_agent_needed(conv_id, "email")
    try:
        await chatwoot.post_private_note(
            conv_id,
            f"🛍️ **Retail enquiry — no Durian showroom for '{city_name}'**\n\n"
            f"The customer's city isn't in our showroom directory → please route "
            f"to customer support ({config.RETAIL_SUPPORT_EMAIL}) to handle.")
    except Exception:
        pass
    if config.REVIEWS_TEAM_ID:
        try:
            await chatwoot.assign_team(conv_id, config.REVIEWS_TEAM_ID)
        except Exception:
            pass
    return [f"🛍️ Retail: city '{city_name}' not in directory — routed to customer support."]


async def _run_retail_gate(conv_id: int, sender_name: str, sender_email: str,
                           subject: str, body: str, *, attempt: int,
                           city_key: str = "") -> list[str]:
    """One pass of the retail gate. Without city_key it resolves the city (and
    lists showrooms / captures a single-showroom owner / routes to support).
    With city_key set (re-entry after listing) it matches the chosen showroom."""
    text = f"{subject}\n{body}"
    name = _resolve_customer_name(sender_name, sender_email)

    if city_key:
        # Re-entry: the customer is picking a showroom from the listed options.
        city_data = retail.CITIES.get(city_key) or {}
        rooms = retail.showrooms(city_data)
        gate = await _retail_gate_llm(name, text, stage="showroom",
                                      showroom_list=retail.list_showrooms_text(city_data))
        idx = gate.get("choice") or 0
        chosen = rooms[idx - 1] if 1 <= idx <= len(rooms) \
            else retail.match_showroom(city_data, text)
        if not chosen:
            return await _retail_ask(
                conv_id, sender_email,
                gate.get("ask_reply") or _retail_showroom_ask(name, city_data),
                {"stage": "showroom", "city_key": city_key}, attempt, "a showroom choice")
        return await _retail_capture_owner(conv_id, sender_email, name,
                                           city_key, city_data, chosen)

    # First pass: resolve the city.
    gate = await _retail_gate_llm(name, text, stage="city")
    city_name = gate.get("city") or ""
    if not city_name:
        return await _retail_ask(
            conv_id, sender_email,
            gate.get("ask_reply") or _RETAIL_CITY_FALLBACK_ASK.format(customer_name=name),
            {"stage": "city"}, attempt, "their city")
    found = retail.lookup_city(city_name)
    if not found:
        return await _retail_route_to_support(conv_id, city_name)
    ckey, city_data = found
    rooms = retail.showrooms(city_data)
    if len(rooms) <= 1:
        if not rooms:
            return await _retail_route_to_support(conv_id, city_name)
        return await _retail_capture_owner(conv_id, sender_email, name,
                                           ckey, city_data, rooms[0])
    # Multiple showrooms → list them and ask which is nearest.
    return await _retail_ask(conv_id, sender_email,
                             _retail_showroom_ask(name, city_data),
                             {"stage": "showroom", "city_key": ckey}, attempt, "a showroom choice")


async def _handle_retail_reply(conv_id: int, data: dict, conv: dict,
                               pending: dict) -> dict:
    """Customer replied on a retail enquiry awaiting a city / showroom choice."""
    content = data.get("content") or ""
    subject = ((data.get("content_attributes") or {}).get("email") or {}).get("subject") or ""
    sender  = (conv.get("meta") or {}).get("sender") or {}
    audit = await _run_retail_gate(
        conv_id, sender.get("name") or "", sender.get("email") or "",
        subject, content, attempt=int(pending.get("attempts") or 1),
        city_key=str(pending.get("city_key") or ""))
    return {"handled": "retail_reply", "audit": audit}


# ── Complaint-details gate ─────────────────────────────────────────────────
# A complaint is forwarded + ticketed only once the customer has given all
# three: order id + registered phone + reason. Missing any → auto-send ONE
# empathetic ask and hold. After the cap, forward + ticket anyway (never lose
# a complaint). The captured slots populate the Zoho ticket.
COMPLAINT_DETAILS_NEEDED_LABEL = "complaint-details-needed"

_COMPLAINT_DETAILS_FALLBACK_ASK = """Dear {customer_name},

Thank you for registering your complaint, and we're truly sorry for the trouble.

So we can escalate this to the right team quickly, could you please share:
1. Your order ID (online orders) or invoice/bill number (store purchases)
2. The phone number the order was registered with
3. A brief description of the issue

As soon as we have these, we'll raise it with our support team right away.

Regards,
Team Durian"""


async def _complaint_gate_llm(customer_name: str, text: str,
                              have_phone: bool) -> dict:
    """One LLM call for the complaint gate: judge whether the message states a
    clear REASON for the complaint (what went wrong / which product), and —
    when order id / phone / reason is still missing — draft a warm, empathetic
    reply asking ONLY for what's missing. Returns {"reason": str, "ask_reply":
    str}; reason is a short summary (empty if none stated), ask_reply is "" when
    nothing is missing. Best-effort — empties on failure so the flow never breaks."""
    from llm_client import client
    schema = {
        "name": "complaint_gate", "strict": True,
        "schema": {"type": "object", "additionalProperties": False,
                   "required": ["reason", "order_ref", "ask_reply"],
                   "properties": {"reason": {"type": "string"},
                                  "order_ref": {"type": "string"},
                                  "ask_reply": {"type": "string"}}},
    }
    system = (
        "You are a warm, empathetic customer-support agent for Durian, an Indian "
        "furniture brand, handling a customer COMPLAINT.\n"
        "Return three fields:\n"
        "1. reason — a short (<=140 char) summary of the specific complaint if "
        "the customer states one (the product + what's wrong, e.g. 'recliner "
        "leather peeling within warranty'). Empty string if the message is only "
        "a vague grievance with no actionable specifics ('worst brand', 'very "
        "bad service') and no product/issue named.\n"
        "2. order_ref — the customer's ORDER ID or INVOICE / BILL number if the "
        "message provides one, copied verbatim. ACCEPT a bare value with no "
        "keyword (e.g. '83452 HG', 'D#12345', 'Invoice 948', 'Bill 7788') — this "
        "is how the customer identifies their purchase: an order id for online "
        "orders OR an invoice/bill number for offline store purchases. Empty "
        "string if none is provided. Do NOT treat a GST number, a quantity "
        "(e.g. '8342 hg'), a price/amount, a pincode, or a 10-digit phone number "
        "as an order_ref.\n"
        "3. ask_reply — to escalate we REQUIRE (a) an order id OR invoice/bill "
        "number, (b) the registered phone, and (c) a clear reason. The reference "
        "is PRESENT if and only if you set order_ref above; the phone is present "
        "per 'Already have phone' below; the reason is present if you set a "
        "non-empty reason. If ANY of the three is still missing, write a short, "
        "warm reply that thanks them for registering the complaint, says we're "
        "sorry to hear it, and asks ONLY for what's still missing — when asking "
        "for the reference, ask for 'your order ID or invoice/bill number'. Never "
        "ask for something already provided. "
        f"Address them as '{customer_name}', plain text, sign off exactly:\n"
        "Regards,\nTeam Durian\n"
        "If all three are already present, return ask_reply as an empty string."
    )
    user = (f"Already have phone: {have_phone}\n\n"
            f"COMPLAINT MESSAGE:\n{text[:2000]}")
    try:
        resp = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        parsed = json.loads(resp.choices[0].message.content) or {}
        return {"reason": (parsed.get("reason") or "").strip(),
                "order_ref": (parsed.get("order_ref") or "").strip(),
                "ask_reply": (parsed.get("ask_reply") or "").strip()}
    except Exception as e:
        print(f"[complaint-gate] LLM gate failed: {e}")
        return {"reason": "", "order_ref": "", "ask_reply": ""}


async def _run_complaint_details_gate(conv_id: int, sender_name: str,
                                      sender_email: str, subject: str, body: str,
                                      *, attempt: int) -> tuple[list[str], bool]:
    """One pass of the complaint gate. Returns (audit, proceed).
    proceed=True  → all three slots present, OR the ask cap was reached
                    (forward + ticket anyway); the caller runs its normal
                    forward/ticket, and the captured slots are on
                    complaint_details for the ticket.
    proceed=False → a detail was missing and we auto-sent the ask; the caller
                    MUST skip the forward, ticket, and acknowledgment this pass."""
    text = f"{subject}\n{body}"
    order_ids = _extract_order_ids(text)
    phones    = _extract_phones(text)
    have_phone = bool(phones)
    name = _resolve_customer_name(sender_name, sender_email)

    gate = await _complaint_gate_llm(name, text, have_phone)
    reason = gate.get("reason") or ""
    have_reason = bool(reason)
    # Order reference: prefer the keyword/D#-matched regex value; otherwise
    # accept the bare order id / invoice-bill number the LLM pulled from the
    # message — so a customer who just replies "83452 HG" (no keyword) is
    # recognised, and an offline invoice/bill number counts too.
    order_ref = order_ids[0] if order_ids else (gate.get("order_ref") or "").strip()
    have_order = bool(order_ref)

    def _capture(partial: bool) -> dict:
        return {"order_id": order_ref,
                "phone": phones[0] if phones else "",
                "reason": reason, "captured_at": _now_iso(),
                **({"partial": True} if partial else {})}

    if have_order and have_phone and have_reason:
        await chatwoot.merge_custom_attributes(conv_id, {
            "complaint_details": _capture(False), "pending_complaint_details": None})
        try:
            await chatwoot.remove_label(conv_id, COMPLAINT_DETAILS_NEEDED_LABEL)
        except Exception:
            pass
        return ["🎫 Complaint details complete (order id + phone + reason) — "
                "forwarding + ticketing."], True

    missing = ", ".join(x for x in ("order id" if not have_order else "",
                                    "phone" if not have_phone else "",
                                    "reason" if not have_reason else "") if x)
    if attempt >= config.COMPLAINT_DETAILS_MAX_ASKS:
        # Never lose a complaint: proceed with whatever we have, flagged partial.
        await chatwoot.merge_custom_attributes(conv_id, {
            "complaint_details": _capture(True), "pending_complaint_details": None})
        await _flag_agent_needed(conv_id, "email")
        return [f"🎫 Complaint details ({missing}) still missing after {attempt} "
                f"ask(s) — forwarding + ticketing anyway."], True

    ask = gate.get("ask_reply") or _COMPLAINT_DETAILS_FALLBACK_ASK.format(customer_name=name)
    await chatwoot.merge_custom_attributes(conv_id, {
        "pending_complaint_details": {"attempts": attempt + 1, "asked_at": _now_iso()}})
    if sender_email:
        try:
            await chatwoot.send_outgoing_message(conv_id, ask, to_emails=sender_email)
        except Exception as e:
            print(f"[complaint-gate] ask send failed for conv {conv_id}: {e}")
    await _label_conversation(conv_id, COMPLAINT_DETAILS_NEEDED_LABEL)
    print(f"[complaint-gate] conv {conv_id}: auto-sent ask for {missing} (attempt {attempt + 1})")
    return [f"🎫 Complaint details ({missing}) missing — auto-sent request; "
            f"holding forward + ticket."], False


def _complaint_thread_transcript(messages: list, sender_name: str,
                                 max_chars: int = 8000) -> str:
    """The full PUBLIC back-and-forth of the complaint (oldest first), Customer
    / Team Durian labelled — forwarded to the team so they have the whole
    thread, not just the first message. Private agent notes are excluded."""
    lines = []
    for m in messages:
        if m.get("private"):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        mtype = m.get("message_type")
        if mtype in (0, "incoming"):
            who = sender_name or "Customer"
        elif mtype in (1, "outgoing"):
            who = "Team Durian"
        else:
            continue
        lines.append(f"{who}:\n{content}")
    return "\n\n----\n\n".join(lines)[:max_chars]


async def _handle_complaint_details_reply(conv_id: int, data: dict,
                                          conv: dict, pending: dict) -> dict:
    """Customer replied on a complaint awaiting details. Re-runs the gate; if it
    now has everything (or hits the cap) it runs the full complaint action layer
    (forward + ticket) via _phase2_execute_actions."""
    reply   = data.get("content") or ""
    sender  = (conv.get("meta") or {}).get("sender") or {}
    name, email = sender.get("name") or "", sender.get("email") or ""
    # The ORIGINAL complaint (first incoming) is what we forward to the team —
    # not the customer's reply (which quotes our ask email). It also gives the
    # gate full context: the reason usually lives in the original complaint,
    # while the order id / phone come in the reply.
    try:
        messages = await chatwoot.get_conversation_messages(conv_id)
    except Exception:
        messages = []
    orig_subject, orig_content = _conv_first_incoming_body(messages)
    orig_subject = orig_subject or \
        ((data.get("content_attributes") or {}).get("email") or {}).get("subject") or ""
    combined = "\n\n".join(t for t in (orig_content, reply) if t).strip() or reply
    audit, proceed = await _run_complaint_details_gate(
        conv_id, name, email, orig_subject, combined,
        attempt=int(pending.get("attempts") or 1))
    if not proceed:
        print(f"[complaint-gate] conv {conv_id}: reply re-entry → still missing")
        return {"handled": "complaint_details_reply", "audit": audit}
    # Details complete (or cap) → run the complaint action layer, forwarding the
    # ENTIRE thread (complaint → our ask → their reply) rather than one message.
    custom = conv.get("custom_attributes") or {}
    category_result = custom.get("email_category_v2") or {"category": "complaint",
                                                          "action": "forward"}
    rule = (classifier._ROUTING_RULES.get("categories") or {}).get("complaint")
    thread = _complaint_thread_transcript(messages, name) or (orig_content or reply)
    extra = await _phase2_execute_actions(
        conv_id, category_result, rule, name, email,
        thread, orig_subject,
        email_category="legitimate", complaint_gate_done=True)
    print(f"[complaint-gate] conv {conv_id}: details complete → ran action layer")
    return {"handled": "complaint_details_reply", "audit": audit + (extra or [])}


async def _phase2_execute_actions(conv_id: int,
                                  category_result: dict,
                                  rule: Optional[dict],
                                  sender_name: str,
                                  sender_email: str,
                                  original_content: str,
                                  original_subject: str,
                                  email_category: str = "",
                                  manual: bool = False,
                                  complaint_gate_done: bool = False) -> list[str]:
    """Phase 2B action layer: when PHASE_2_DRY_RUN is off, ACTUALLY send the
    acknowledgment + (for forward categories) the forwarded email via
    Chatwoot's outbound channel. Both use the conversation's existing
    SMTP — no new email credentials anywhere.

    Returns a list of markdown lines to append to the audit private note
    so the agent sees a short "📤 Sent acknowledgment / 📤 Forwarded to X"
    summary inline with the categorizer's decision."""
    cat_key = category_result.get("category") or "fallback"
    action  = category_result.get("action")   or "in_channel"
    name    = _resolve_customer_name(sender_name, sender_email)
    template = _resolve_acknowledgment_template(cat_key)
    audit: list[str] = []

    # Deal-vertical lead → an agent still has to click Create Deal. Surface it in
    # the unified "Agent needs" section (Email channel) now; _clear_agent_needed
    # fires when the deal is created (create-deal endpoint) or the conv resolves.
    if cat_key in _DEAL_VERTICAL_LABEL:
        await _flag_agent_needed(conv_id, "email")

    # ── Complaint-details gate ──────────────────────────────────────────
    # Before forwarding a complaint to support AND creating its Zoho ticket,
    # require order id + registered phone + reason. Missing any → auto-send one
    # empathetic ask and STOP this pass (no ack, no forward, no ticket); the
    # customer's reply re-enters via _handle_complaint_details_reply. Skipped
    # when the re-entry already ran the gate (complaint_gate_done).
    if (cat_key == "complaint" and config.COMPLAINT_DETAILS_GATE_ENABLED
            and not complaint_gate_done):
        gate_audit, proceed = await _run_complaint_details_gate(
            conv_id, sender_name, sender_email,
            original_subject, original_content, attempt=0)
        if not proceed:
            return gate_audit

    # ── Customer acknowledgment ─────────────────────────────────────────
    # Sent only when EMAIL_CUSTOMER_ACK_ENABLED=true. During the prod test
    # phase the flag is off — forwards still run, but no customer email
    # goes out. Flip the env var on the VM when the client is ready.
    #
    # Pass the customer email as an EXPLICIT to_emails. Without it,
    # Chatwoot's defaulting-to-contact behaviour proved unreliable
    # (the conv 192 incident) — and Chatwoot's mailer reads to_emails
    # from the latest outgoing message rather than the in-flight one, so
    # the explicit recipient is what makes the upstream-patched mailer
    # do the right thing.
    # Complaint acks are held back and sent AFTER the Zoho ticket is created
    # (below), so the customer's reference number can ride along in the same
    # email. Stashed as (recipient, base_body) when that path applies.
    deferred_complaint_ack: Optional[tuple[str, str]] = None
    if not _EMAIL_CUSTOMER_ACK_ENABLED:
        audit.append("ℹ️ Customer acknowledgment is disabled (flag off).")
    elif cat_key in _NO_ACK_CATEGORIES:
        # General-information (and similar FYI categories) don't warrant an
        # acknowledgment — the customer isn't waiting on a routed reply.
        audit.append(
            f"ℹ️ Acknowledgment skipped — '{cat_key}' is a no-acknowledgment "
            f"category."
        )
    elif email_category == "automated" or _is_no_reply_sender(sender_email):
        # Automated / OTP / third-party no-reply mail has no human recipient —
        # never acknowledge it (the forward, if any, still runs below).
        audit.append(
            f"ℹ️ Acknowledgment skipped — automated / no-reply sender "
            f"({sender_email})."
        )
    elif cat_key == "existing_order_enquiry" and config.ORDER_LOOKUP_ENABLED:
        # The BMS order-lookup flow (below) owns the customer-facing reply for
        # existing-order enquiries — it thanks the customer AND either shares
        # the order details or asks for the required order id + phone in ONE
        # email. Sending the generic acknowledgment too would be a duplicate.
        audit.append(
            "ℹ️ Acknowledgment skipped — order-lookup owns the customer reply."
        )
    elif cat_key in _DEAL_DETAILS_CATEGORIES and config.DEAL_DETAILS_GATE_ENABLED:
        # The deal-details gate (below) owns the reply for bulk order / FHC /
        # door: it either asks for the required phone + city or acknowledges the
        # registered enquiry — one email either way, so skip the generic ack.
        audit.append(
            "ℹ️ Acknowledgment skipped — deal-details gate owns the customer reply."
        )
    elif cat_key == "product_enquiry" and config.RETAIL_ROUTING_ENABLED:
        # The retail routing gate (below) owns the reply for product enquiries —
        # it asks for the city or lists the city's showrooms in ONE email, so the
        # generic acknowledgment would be a duplicate.
        audit.append(
            "ℹ️ Acknowledgment skipped — retail routing gate owns the customer reply."
        )
    elif template and sender_email:
        ack_body = (template.get("body") or "").format(
            customer_name    = name,
            original_subject = original_subject or "",
        )
        if cat_key == "complaint":
            # Defer: the ticket doesn't exist yet. Sent after ticket creation
            # so we can append the reference number for the customer.
            deferred_complaint_ack = (sender_email, ack_body)
        else:
            try:
                await chatwoot.send_outgoing_message(
                    conv_id, ack_body, to_emails=sender_email
                )
                audit.append(f"✅ Acknowledgment sent to the customer ({sender_email}).")
            except Exception as e:
                print(f"[phase2b] acknowledgment send failed for conv {conv_id}: {e}")
                audit.append(f"⚠️ Acknowledgment could not be sent: {e}")

    # ── Forward to the concerned department (forward categories only) ──
    # Uses Chatwoot's `to_emails` override so the email goes to the
    # department address (NOT the customer). cc/bcc applied per the YAML
    # rule; customer optionally Cc'd when the rule says so.
    forwarded_ok = False
    if action == "forward" and rule:
        forward_to = (rule.get("forward_to") or "").strip()
        cc_list    = list(rule.get("cc") or [])
        if rule.get("include_customer_in_cc") and sender_email:
            cc_list.append(sender_email)
        bcc_list   = list(rule.get("bcc") or [])

        if not forward_to:
            audit.append("⚠️ Forward skipped: no `forward_to` in routing rule")
        else:
            # Compose the forwarded body. Customer-safe wording: the
            # customer is Cc'd on this email for complaint/legal/doors, so
            # it must NOT expose internal routing jargon ("auto-forwarded by
            # routing bridge", category enum, etc.). Reads like a normal
            # professional internal forward of the customer's message.
            if cat_key == "complaint":
                fwd_lines = [f"Forwarding this complaint from {sender_name or sender_email} "
                             "for your review and necessary action.", ""]
                # Structured slots gathered by the complaint-details gate, so the
                # team has the key facts up front.
                try:
                    _cd = ((await chatwoot.get_conversation(conv_id)).get(
                        "custom_attributes") or {}).get("complaint_details") or {}
                except Exception:
                    _cd = {}
                det = [f"Reason: {_cd['reason']}" if _cd.get("reason") else "",
                       f"Order ID: {_cd['order_id']}" if _cd.get("order_id") else "",
                       f"Registered phone: {_cd['phone']}" if _cd.get("phone") else ""]
                det = [d for d in det if d]
                if det:
                    fwd_lines += det + [""]
            else:
                fwd_lines = [f"Forwarding the message below from "
                             f"{sender_name or sender_email} for your review and "
                             "necessary action.", ""]
            fwd_lines += [
                f"From: {sender_name or '(unknown)'} <{sender_email}>",
                f"Subject: {original_subject or '(no subject)'}",
                "",
                "----------------------------------------",
                "",
                original_content.strip(),
                "",
                "----------------------------------------",
                "Regards,",
                "Team Durian",
            ]
            forward_body = "\n".join(fwd_lines)
            # suppress_forward: keep the full sector/region classification (it
            # drives CRM deal creation) but DON'T email any team — the client
            # wants bulk orders qualified in-channel, not forwarded. The
            # sector/region audit + labels below still run for agent context.
            suppress = bool(rule.get("suppress_forward"))
            try:
                if suppress:
                    audit.append("📭 Not forwarded to any team (client policy) — "
                                 "the lead is qualified in-channel; create the deal "
                                 "to resolve.")
                else:
                    await chatwoot.send_outgoing_message(
                        conv_id,
                        forward_body,
                        to_emails  = forward_to,
                        cc_emails  = ", ".join(cc_list)  if cc_list  else None,
                        bcc_emails = ", ".join(bcc_list) if bcc_list else None,
                    )
                    forwarded_ok = True
                    audit.append(f"📨 Forwarded to {forward_to}.")
                # For bulk orders, show which sector (government/private) drove
                # the destination so the agent sees the routing decision.
                if category_result.get("sector"):
                    _sec = category_result["sector"]
                    _sc  = int(round(float(category_result.get("sector_confidence") or 0) * 100))
                    audit.append(
                        f"🏛️ Buyer sector: **{_sec}** ({_sc}% confident)"
                        + (f" — {category_result['sector_reason']}"
                           if category_result.get("sector_reason") else ""))
                # For private bulk orders, show the region that drove routing.
                if category_result.get("region"):
                    _rg = category_result["region"]
                    _rc = int(round(float(category_result.get("region_confidence") or 0) * 100))
                    audit.append(
                        f"🗺️ Region: **{_rg}** ({_rc}% confident)"
                        + (f" — {category_result['region_reason']}"
                           if category_result.get("region_reason") else ""))
                # For doors, show which location bucket drove the destination.
                if category_result.get("doors_location"):
                    _loc = category_result["doors_location"]
                    _lc  = int(round(float(category_result.get("doors_location_confidence") or 0) * 100))
                    audit.append(
                        f"📍 Doors location: **{_loc}** ({_lc}% confident)"
                        + (f" — {category_result['doors_location_reason']}"
                           if category_result.get("doors_location_reason") else ""))
                if cc_list:
                    audit.append(f"Cc: {', '.join(cc_list)}")

                # Tag the conversation so agents can find these emails in one
                # place via the sidebar's Email-handling views. An agent-confirmed
                # send (manual=True) is tagged `manually-sent`; a fully automatic
                # forward is tagged `auto-forwarded`. Best-effort — must not undo
                # the forward.
                # Skip the forwarded/manually-sent tag when nothing was actually
                # sent (suppress_forward) — those labels drive the "Auto-forwarded"
                # sidebar view, which must not list un-forwarded conversations.
                if not suppress:
                    fwd_label = "manually-sent" if manual else "auto-forwarded"
                    try:
                        await chatwoot.add_label(conv_id, fwd_label)
                        audit.append(f"🏷️ Tagged {fwd_label}.")
                    except Exception as e:
                        print(f"[phase2b] add_label failed for conv {conv_id}: {e}")

                # Bulk orders also get a sector label (bulk-government /
                # bulk-private) so agents can see + filter the buyer sector at a
                # glance, not just read it in the note.
                if category_result.get("sector"):
                    sec_label = f"bulk-{category_result['sector']}"
                    try:
                        await chatwoot.add_label(conv_id, sec_label)
                        audit.append(f"🏷️ Tagged {sec_label}.")
                    except Exception as e:
                        print(f"[phase2b] sector add_label failed for conv {conv_id}: {e}")

                # Private bulk orders also get a region label (region-karnataka …).
                if category_result.get("region") and category_result.get("region") != "other":
                    rg_label = f"region-{category_result['region']}"
                    try:
                        await chatwoot.add_label(conv_id, rg_label)
                        audit.append(f"🏷️ Tagged {rg_label}.")
                    except Exception as e:
                        print(f"[phase2b] region add_label failed for conv {conv_id}: {e}")

                # Doors get a location label (doors-bangalore / doors-other).
                if category_result.get("doors_location"):
                    loc_label = f"doors-{category_result['doors_location']}"
                    try:
                        await chatwoot.add_label(conv_id, loc_label)
                        audit.append(f"🏷️ Tagged {loc_label}.")
                    except Exception as e:
                        print(f"[phase2b] doors add_label failed for conv {conv_id}: {e}")
            except Exception as e:
                print(f"[phase2b] forward send failed for conv {conv_id}: {e}")
                audit.append(f"⚠️ Forward could not be sent: {e}")
    elif action == "in_channel":
        # In-channel categories aren't forwarded — the conversation stays
        # open for an agent to handle. Say so plainly in the note.
        audit.append("This conversation stays here for the team to assist.")

    # ── BMS order lookup (existing-order enquiries only) ────────────────
    # Fetch the customer's order(s) and draft everything for the agent —
    # or draft a "please share your order no. / phone" ask when the email
    # carries no identifiers. Best-effort: a lookup failure produces an
    # audit line, never blocks the ack/CRM steps around it.
    if cat_key == "existing_order_enquiry" and config.ORDER_LOOKUP_ENABLED:
        try:
            audit += await _run_order_lookup(
                conv_id, sender_name, sender_email,
                original_subject, original_content, attempt=0)
        except Exception as e:
            print(f"[order-lookup] unexpected error for conv {conv_id}: {e}")
            audit.append(f"⚠️ BMS order lookup errored: {e}")

    # ── Deal-details gate (bulk order / FHC / door) ─────────────────────
    # Require the customer's phone + city before a deal can be created. When
    # missing, auto-send an AI-drafted request (replacing the generic ack);
    # when present, capture them + acknowledge and mark the enquiry deal-ready.
    if cat_key in _DEAL_DETAILS_CATEGORIES and config.DEAL_DETAILS_GATE_ENABLED:
        try:
            audit += await _run_deal_details_gate(
                conv_id, sender_name, sender_email,
                original_subject, original_content, cat_key, attempt=0)
        except Exception as e:
            print(f"[deal-gate] unexpected error for conv {conv_id}: {e}")
            audit.append(f"⚠️ Deal-details gate errored: {e}")

    # ── Retail routing gate (product enquiry → city → showroom → owner) ──
    # Retail furniture purchase enquiries route to a showroom's CRM owner: ask
    # the city, list the city's showrooms, capture the chosen owner for Create
    # Deal. Owns the customer reply (generic ack suppressed above).
    if cat_key == "product_enquiry" and config.RETAIL_ROUTING_ENABLED:
        try:
            audit += await _run_retail_gate(
                conv_id, sender_name, sender_email,
                original_subject, original_content, attempt=0)
        except Exception as e:
            print(f"[retail-gate] unexpected error for conv {conv_id}: {e}")
            audit.append(f"⚠️ Retail gate errored: {e}")

    # ── Zoho CRM Contact + Note (qualifying categories only) ────────────
    # Fires for product enquiries / general info / existing-order — the
    # sales-shaped categories. Manual "Push to CRM as Lead/Deal" buttons
    # (PR B) reuse the crm_contact_id we stash here. Best-effort: any CRM
    # failure produces an audit line but doesn't affect ack/forward above.
    if cat_key in config.ZOHO_CRM_AUTO_CATEGORIES:
        ack_sent = any(line.startswith("✅ Acknowledgment sent") for line in audit)
        category_display = (rule or {}).get("display_name") or cat_key
        try:
            crm_line = await _push_contact_and_note(
                conv_id           = conv_id,
                sender_name       = sender_name,
                sender_email      = sender_email,
                subject           = original_subject,
                message_body      = original_content,
                category_key      = cat_key,
                category_display  = category_display,
                ack_sent          = ack_sent,
            )
            if crm_line:
                audit.append(crm_line)
        except Exception as e:
            # find_or_create_contact / create_note already handle their own
            # errors; this catch is for anything unexpected higher up.
            print(f"[crm] unexpected error for conv {conv_id}: {e}")
            audit.append(f"⚠️ CRM push errored unexpectedly: {e}")

    # Product complaints ALSO spin up an assigned Zoho Desk ticket (on top of
    # the email forward + ack above). Only after a successful forward, so a
    # failed handoff doesn't leave a ticket with no email trail.
    ticket_decision_pending = False
    complaint_ticket: Optional[dict] = None
    if forwarded_ok and cat_key == "complaint":
        ticket_lines, ticket_decision_pending, complaint_ticket = \
            await _maybe_create_complaint_ticket(
                conv_id, sender_name, sender_email,
                subject=original_subject, body=original_content)
        audit += ticket_lines
    elif cat_key != "complaint":
        # Non-complaint messages can still quote a ticket number ("what is
        # the status of ticket #253?" often classifies as an order enquiry).
        # No dedup stakes here — nothing is being created — but the agent
        # shouldn't have to hand-search Zoho for a number the customer
        # already gave us. Lookup + link it in the audit note.
        refs = _extract_ticket_refs(f"{original_subject}\n{original_content}")
        for ref in refs:
            try:
                t = await zoho.get_ticket_by_number(ref)
            except Exception as e:
                print(f"[zoho] ref #{ref} lookup failed: {e}")
                continue
            if t:
                link = f"[#{t['number']}]({t['url']})" if t.get("url") else f"#{t['number']}"
                audit.append(
                    f"🔎 Message references Zoho ticket {link} — "
                    f"{t.get('status')}: {(t.get('subject') or '')[:80]}")

    # ── Send the deferred complaint acknowledgment ──────────────────────
    # Sent with the reference number once the ticket is CREATED. When the dedup
    # paused for an agent decision (no ticket yet), HOLD the ack — it goes out
    # from the decision panel the moment the agent creates/attaches the ticket
    # (_send_complaint_reference_ack), so the customer gets ONE email with the
    # reference, not a number-less one now and another later.
    if deferred_complaint_ack and ticket_decision_pending:
        audit.append("⏸️ Complaint ack held — ticket decision pending; it goes "
                     "out with the reference once the ticket is created.")
    elif deferred_complaint_ack:
        recipient, ack_body = deferred_complaint_ack
        ref_num = complaint_ticket.get("ticketNumber") if complaint_ticket else None
        if ref_num:
            ack_body = _append_ticket_reference(ack_body, ref_num)
        try:
            await chatwoot.send_outgoing_message(
                conv_id, ack_body, to_emails=recipient
            )
            suffix = f" with reference #{ref_num}" if ref_num else ""
            audit.append(
                f"✅ Acknowledgment sent to the customer ({recipient}){suffix}.")
        except Exception as e:
            print(f"[phase2b] complaint acknowledgment send failed for "
                  f"conv {conv_id}: {e}")
            audit.append(f"⚠️ Acknowledgment could not be sent: {e}")

    # Mark the conversation as Phase-2-handled so subsequent webhook fires
    # on this conv (e.g. department replies landing as new incoming
    # messages) don't re-trigger the whole classify-and-forward dance —
    # those need human handling, not another auto-forward loop.
    try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "phase2_handled_at": _now_iso(),
            "phase2_action":     action,
            "phase2_category":   cat_key,
        })
    except Exception as e:
        print(f"[phase2b] failed to mark phase2_handled_at: {e}")

    # ── Auto-resolve once forwarded ──────────────────────────────────────
    # A forwarded conversation is DONE from the inbox's point of view — the
    # internal team owns it now. Resolving keeps the open queue equal to
    # "work still needing a human" (clean reports). Applies to BOTH auto
    # forwards and agent-confirmed sends (both run through this function).
    # Only after a SUCCESSFUL forward — a failed/skipped forward stays open.
    # A customer reply auto-reopens the conversation in Chatwoot, so nothing
    # gets lost. In-channel categories never reach here (forwarded_ok=False).
    # EXCEPT when the complaint dedup paused for an agent decision: resolving
    # would bury the pending attach-vs-create choice in the resolved queue,
    # and nothing would ever surface it again.
    if ticket_decision_pending:
        audit.append("ℹ️ Left open — a Zoho ticket decision is pending above.")
    elif forwarded_ok and config.RESOLVE_AFTER_FORWARD:
        try:
            await chatwoot.toggle_status(conv_id, "resolved")
            audit.append("✅ Conversation resolved (handed to the team).")
        except Exception as e:
            print(f"[phase2b] auto-resolve failed for conv {conv_id}: {e}")
            audit.append(f"⚠️ Auto-resolve failed: {e}")

    return audit


def _render_phase2_dry_run_preview(category_result: dict,
                                   rule: Optional[dict],
                                   sender_name: str,
                                   sender_email: str,
                                   original_subject: str) -> list[str]:
    """Render the dry-run preview as a list of markdown lines, ready to be
    appended to the categorizer's private note. Returns an empty list when
    Phase 2A is disabled or when there's nothing to preview."""
    if not _PHASE_2_DRY_RUN:
        return []

    cat_key = category_result.get("category") or "fallback"
    action  = category_result.get("action")   or "in_channel"
    name    = _resolve_customer_name(sender_name, sender_email)
    template = _resolve_acknowledgment_template(cat_key)

    # Review-mode banner — plain language, no "Phase 2A / dry-run" jargon.
    lines = ["_⏳ Automated handling is in review mode — nothing has been "
             "sent yet. This is a preview of what would happen:_"]

    if action == "forward" and rule:
        forward_to = rule.get("forward_to") or "(not configured)"
        cc_list    = rule.get("cc") or []
        bcc_list   = rule.get("bcc") or []
        include_customer = bool(rule.get("include_customer_in_cc"))
        cc_effective = list(cc_list) + ([sender_email] if include_customer and sender_email else [])

        lines.append("")
        lines.append(f"📨 Would forward to **{forward_to}**.")
        if cc_effective:
            lines.append(f"Cc: {', '.join(cc_effective)}")
        if bcc_list:
            lines.append(f"Bcc: {', '.join(bcc_list)}")

    if template:
        body = (template.get("body") or "").format(
            customer_name    = name,
            original_subject = original_subject or "",
        )
        lines.append("")
        lines.append("✅ Would acknowledge the customer with:")
        for body_line in body.rstrip().splitlines():
            lines.append(f"> {body_line}" if body_line else ">")
    elif action == "in_channel":
        lines.append("")
        lines.append("This conversation would stay here for the team to assist.")

    return lines


# ── Document extraction: bills / receipts / order screenshots ─────────────
# Fire-and-forget task scheduled by handle_message_created for every
# incoming non-comment message. Gating (attachment types, bill-ish text
# regex), idempotency (per-attachment / per-message source keys) and all
# LLM plumbing live in document_extractor; this wrapper owns persistence
# and the agent-facing private note. Never raises.
MAX_TRACKED_DOCUMENTS = 20

# Strong references to in-flight extraction tasks. asyncio's event loop
# only holds WEAK references to tasks — a bare create_task() with the
# return value dropped can be garbage-collected MID-FLIGHT (documented
# CPython behaviour), silently killing the extraction. Tasks remove
# themselves on completion via the done-callback.
_EXTRACTION_TASKS: set = set()

# Serialises the read-merge-write on extracted_documents. Without it, two
# messages arriving in quick succession run concurrent tasks that both
# read the array before either writes — the slower write silently drops
# the faster one's documents (merge_custom_attributes is read-modify-
# write, not atomic). Single-process bridge → an asyncio.Lock suffices.
_EXTRACTION_WRITE_LOCK = asyncio.Lock()


def _schedule_document_extraction(data: dict) -> None:
    task = asyncio.create_task(_maybe_extract_documents(data))
    _EXTRACTION_TASKS.add(task)
    task.add_done_callback(_EXTRACTION_TASKS.discard)


def _format_document_note(doc: dict) -> str:
    type_label = (doc.get("document_type") or "document").replace("_", " ").title()
    bits = [f"📄 **{type_label} detected**"]
    if doc.get("order_id"):
        bits.append(f"Order `{doc['order_id']}`")
    if doc.get("invoice_number"):
        bits.append(f"Invoice `{doc['invoice_number']}`")
    if doc.get("amount"):
        bits.append(f"{doc.get('currency') or ''} {doc['amount']}".strip())
    if doc.get("document_date"):
        bits.append(doc["document_date"])
    if doc.get("merchant"):
        bits.append(doc["merchant"])
    note = " — ".join([bits[0], " · ".join(bits[1:])]) if len(bits) > 1 else bits[0]
    if doc.get("issue_hint"):
        note += f"\n\n_Issue:_ {doc['issue_hint']}"
    extras = doc.get("other_details") or []
    if extras:
        lines = "\n".join(f"• {d.get('label')}: {d.get('value')}"
                          for d in extras[:8] if d.get("label"))
        note += f"\n\n_Details:_\n{lines}"
    return note


async def _maybe_extract_documents(data: dict) -> None:
    try:
        conv = data.get("conversation") or {}
        conv_id = conv.get("id")
        if not conv_id:
            return

        attachments = data.get("attachments") or []
        content = data.get("content") or ""
        message_id = data.get("id")

        # Cheap pre-gate before any network/LLM work.
        has_media = any(a.get("file_type") in ("image", "file") for a in attachments)
        if not has_media and not document_extractor.text_looks_billish(content):
            return

        # Idempotency: source keys of everything already extracted on this
        # conversation. Webhook payload may be stale, so refetch.
        try:
            conv_data = await chatwoot.get_conversation(conv_id)
            existing_docs = (conv_data.get("custom_attributes") or {}) \
                .get("extracted_documents") or []
        except Exception:
            existing_docs = (conv.get("custom_attributes") or {}) \
                .get("extracted_documents") or []
        seen_keys = {d.get("source_key") for d in existing_docs}

        # Runs in a detached task, so nest via explicit ids (not OTel context).
        _lf = tracing.message_parent(conv_id, message_id, name="document-extraction")
        results = await document_extractor.extract_for_message(
            content, attachments, message_id, seen_keys, lf_parent=_lf
        )
        if not results:
            return

        for doc in results:
            doc["extracted_at"] = _now_iso()
            doc["message_id"] = message_id

        # Persist under the lock: re-read the authoritative array, drop any
        # result another concurrent task already stored (webhook retries),
        # then merge newest-first, deduped by source_key, capped — same
        # shape discipline as zoho_tickets. The slow LLM work above stays
        # OUTSIDE the lock; only the read-merge-write is serialised.
        async with _EXTRACTION_WRITE_LOCK:
            try:
                conv_data = await chatwoot.get_conversation(conv_id)
                existing_docs = (conv_data.get("custom_attributes") or {}) \
                    .get("extracted_documents") or []
            except Exception:
                pass  # keep the pre-extraction snapshot from above
            stored_keys = {d.get("source_key") for d in existing_docs}
            results = [r for r in results if r.get("source_key") not in stored_keys]
            if not results:
                return
            merged = (results + existing_docs)[:MAX_TRACKED_DOCUMENTS]
            await chatwoot.merge_custom_attributes(conv_id, {
                "extracted_documents": merged,
            })

        for doc in results:
            try:
                await chatwoot.post_private_note(conv_id, _format_document_note(doc))
            except Exception as e:
                print(f"[docs] private note failed for conv {conv_id}: {e}")

        print(f"[docs] conv {conv_id}: extracted {len(results)} document(s) "
              f"({', '.join(d.get('document_type') or '?' for d in results)})")
    except Exception as e:  # noqa: BLE001 — fire-and-forget task, never raise
        print(f"[docs] extraction task failed: {type(e).__name__}: {e}")


# ── Handler: first incoming message → spam pipeline + classify + assign ───
async def handle_message_created(data: dict) -> dict:
    msg_type = data.get("message_type")
    print(f"[msg] message_type={msg_type!r}")

    # Outgoing on the reviews inbox = agent's public reply → post to Google.
    if msg_type in (1, "outgoing"):
        return await handle_review_reply(data)

    if msg_type not in (0, "incoming"):
        print(f"[msg] ignoring — not incoming")
        return {"ignored": True, "reason": "not_incoming"}

    # Google Reviews inbox is handled end-to-end by the reviews poller
    # (it ingests the review and drafts the suggestion card). The email
    # pipeline below — categorization card, customer acknowledgment,
    # auto-forwarding, Zoho escalation — must NOT run on reviews.
    inbox      = data.get("inbox") or {}
    inbox_id   = inbox.get("id")
    inbox_type = inbox.get("channel_type") or ""
    if config.REVIEWS_INBOX_ID and inbox_id == config.REVIEWS_INBOX_ID:
        print(f"[msg] ignoring — reviews inbox (handled by reviews poller)")
        return {"ignored": True, "reason": "reviews_inbox"}

    # WhatsApp / Instagram / Facebook DMs. We still let the categorizer LABEL
    # the intent (the "Auto-classified as …" note + team), but the email action
    # layer — customer acknowledgment + auto-forwarding — must NOT run on
    # social: those are email-only features. `is_social` gates the action layer
    # further down.
    is_social = inbox_type in TEMPLATE_CHANNEL_FOR_INBOX_TYPE
    social_channel = TEMPLATE_CHANNEL_FOR_INBOX_TYPE.get(inbox_type)

    conv    = data.get("conversation") or {}
    conv_id = conv.get("id")
    print(f"[msg] conv_id={conv_id}")

    # Comment conversations: when the comment auto-reply bot is ON it owns
    # them (comment-specific prompt in Chatwoot's DmBot). When it's OFF —
    # the prod-test default — agents used to get NOTHING on comments; now
    # they get a template-suggestion card drawn from the PUBLIC comment
    # templates (short, brand-safe, prices redirected to DM). Either way the
    # spam/team pipeline and Zoho escalation never run on comments.
    if is_social and _is_comment_conversation(conv):
        comment_bot_on = os.environ.get(
            "DM_BOT_COMMENT_AUTO_REPLY_ENABLED", "false").lower() == "true"
        if comment_bot_on:
            print(f"[msg] conv {conv_id} is a comment — DM bot replies, skipping pipeline")
            return {"ignored": True, "reason": "comment_bot_handles"}
        full_conv = await chatwoot.get_conversation(conv_id) if conv_id else conv
        return await handle_template_suggest(full_conv, social_channel,
                                             surface="comment")

    # Prod-test phase: the DM bot is OFF (so no customer ever sees a bot
    # message), but the team still wants the Durian template-suggestion card on
    # every incoming social DM so they can review and send the reply manually.
    # When DM_BOT_AUTO_REPLY_ENABLED=true (bot back on), the card waits for
    # handoff as before.
    bot_off = os.environ.get("DM_BOT_AUTO_REPLY_ENABLED", "false").lower() != "true"
    if is_social and bot_off:
        full_conv = await chatwoot.get_conversation(conv_id) if conv_id else conv
        return await handle_template_suggest(full_conv, social_channel)

    # Document extraction (bills / receipts / order screenshots). MUST be
    # scheduled BEFORE the early returns below: the spam/team pipeline only
    # runs on a conversation's FIRST message, but bills arrive at ANY point
    # in a conversation. Fire-and-forget so a slow vision call never delays
    # the webhook response; gating + idempotency live inside the helper.
    if config.DOC_EXTRACTION_ENABLED:
        _schedule_document_extraction(data)

    # Order-lookup re-entry: a conversation waiting on order details gets
    # its customer replies routed back into the lookup flow — this MUST run
    # before the already-classified guard below, which would otherwise
    # swallow the reply ("email_category already set").
    existing_attrs   = conv.get("custom_attributes") or {}
    pending_order = existing_attrs.get("pending_order_lookup")
    if (pending_order and config.ORDER_LOOKUP_ENABLED
            and data.get("message_type") in (0, "incoming")):
        return await _handle_order_lookup_reply(conv_id, data, conv, pending_order)

    # Deal-details re-entry: a bulk/FHC/door enquiry waiting on phone + city
    # gets the customer's reply routed back into the gate (before the
    # already-classified guard, same as order-lookup above).
    pending_deal = existing_attrs.get("pending_deal_details")
    if (pending_deal and config.DEAL_DETAILS_GATE_ENABLED
            and data.get("message_type") in (0, "incoming")):
        return await _handle_deal_details_reply(conv_id, data, conv, pending_deal)

    # Complaint-details re-entry: a complaint waiting on order id + phone +
    # reason gets the customer's reply routed back into the gate.
    pending_complaint = existing_attrs.get("pending_complaint_details")
    if (pending_complaint and config.COMPLAINT_DETAILS_GATE_ENABLED
            and data.get("message_type") in (0, "incoming")):
        return await _handle_complaint_details_reply(conv_id, data, conv, pending_complaint)

    pending_retail = existing_attrs.get("pending_retail")
    if (pending_retail and config.RETAIL_ROUTING_ENABLED
            and data.get("message_type") in (0, "incoming")):
        return await _handle_retail_reply(conv_id, data, conv, pending_retail)

    # Idempotency: already-classified conversations skip both classifiers.
    existing_category = existing_attrs.get("email_category")
    if existing_category:
        print(f"[msg] ignoring — email_category already set: {existing_category}")
        return {"ignored": True, "reason": "already_classified"}

    # Idempotency: team already set means we already handled this conversation.
    team_meta = (conv.get("meta") or {}).get("team")
    if team_meta:
        print(f"[msg] ignoring — team already set: {team_meta}")
        return {"ignored": True, "reason": "team_already_set"}

    content      = data.get("content") or ""
    inbox        = data.get("inbox") or {}
    inbox_name   = inbox.get("name", "")
    sender       = (conv.get("meta") or {}).get("sender") or {}
    sender_email = sender.get("email") or ""
    contact_id   = sender.get("id")
    if not conv_id:
        return {"ignored": True, "reason": "no_conversation_id"}

    # Real email Subject (when this is an email-channel conversation) lives
    # on conversation.additional_attributes.mail_subject. For chat/IG/FB
    # channels there is no subject so we fall back to a snippet of the
    # message body — never the inbox name (review: the inbox name as
    # "subject" pollutes the prompt with constant noise per inbox).
    additional = conv.get("additional_attributes") or {}
    real_subject = (
        additional.get("mail_subject")
        or additional.get("subject")
        or content[:80]
    )

    # ── Automated system / transactional / internal emails ────────────────
    # Two deterministic short-circuits that file mail as General Information,
    # AUTO-RESOLVE it, and STOP — no forward, no CRM Contact, no acknowledgment,
    # no needs-review card, no team routing. Auto-resolve keeps this noise out
    # of the open Conversations list (a customer reply re-opens the thread):
    #   1. Subject matches a system-notification phrase (order/OTP/shipping/
    #      stock alerts) — routing_rules.yaml:system_notification_subjects.
    #   2. An internal auto-file address (e.g. customersupport@durian.in) SENT
    #      the mail (From only). These are the client's own support threads that
    #      CC hello@durian.in. A customer who emails hello@durian.in and merely
    #      CCs customersupport is a real enquiry — preserved and classified.
    # Social DMs have no email subject/headers, so this only applies to email.
    auto_file_reason: Optional[str] = None
    if not is_social:
        if classifier.is_system_notification(real_subject):
            auto_file_reason = "subject-matched system/transactional email"
        else:
            # Match ONLY the From address — the internal address must have SENT
            # the mail to auto-file it. A customer who emails hello@durian.in and
            # merely CCs customersupport is a REAL enquiry that must be preserved
            # and classified normally, so To/Cc are deliberately NOT matched.
            _email_hdr = (data.get("content_attributes") or {}).get("email") or {}
            _from = {sender_email.lower()} if sender_email else set()
            for _a in (_email_hdr.get("from") or []):
                _val = _a.get("email") if isinstance(_a, dict) else _a
                if isinstance(_val, str):
                    _from.add(_val.lower())
            _from.discard("")
            if any(classifier.is_auto_file_sender(a) for a in _from):
                auto_file_reason = "internal auto-file sender (customersupport)"

    if auto_file_reason:
        print(f"[auto-file] conv {conv_id}: {auto_file_reason} — "
              f"General Information + auto-resolve, skipping forward/CRM/ack/review")
        gi_rule = (classifier._ROUTING_RULES.get("categories") or {}).get(
            "general_information") or {}
        try:
            await chatwoot.add_label(conv_id, "general-information")
        except Exception as e:
            print(f"[auto-file] add_label failed: {e}")
        try:
            await chatwoot.merge_custom_attributes(conv_id, {
                "email_category_v2": {
                    "category":     "general_information",
                    "confidence":   1.0,
                    "reason":       f"Auto-filed: {auto_file_reason}.",
                    "action":       "in_channel",
                    "display_name": gi_rule.get("display_name")
                                    or "General Information",
                    "classified_at": _now_iso(),
                },
                # Mark handled so a later reply on the thread doesn't re-run the
                # classify pipeline, and record the intent label for the digest.
                "email_category":    "automated",
                "phase2_handled_at": _now_iso(),
                "phase2_category":   "general_information",
                "system_notification": True,
            })
        except Exception as e:
            print(f"[auto-file] merge_custom_attributes failed: {e}")
        try:
            await chatwoot.toggle_status(conv_id, "resolved")
        except Exception as e:
            print(f"[auto-file] auto-resolve failed: {e}")
        return {"classified_email_type": "auto_filed",
                "category": "general_information",
                "reason": auto_file_reason, "auto_handled": True, "resolved": True}

    # ── Email-type classification pipeline ────────────────────────────────
    # Layers (earliest-exit wins on inbox bypass; otherwise classifier runs):
    #   * NEVER_SPAM_INBOXES — operator-controlled per-inbox bypass
    #   * Classifier runs ALWAYS otherwise (no free pass for known senders).
    #     Classifier output is now a dict containing label, confidence,
    #     escalation_signal and escalation_reason — see classifier.py.
    #   * Sender history acts as TIEBREAKER for low-confidence spam:
    #       contact has prior non-spam convo + classifier says spam@conf<8
    #       → downgrade to 'promotional' instead of leaving as spam
    #   * Spam: auto-SNOOZED (not resolved) so it lives in its own tab and
    #     doesn't pollute Resolved-tab reports. Customer reply auto-reopens.

    email_category: str = "legitimate"
    classifier_conf: int = 0
    escalation_signal: str = "none"
    escalation_reason_text: str = ""
    bypass_reason: Optional[str] = None
    sender_history_count: int = 0

    if inbox_name and inbox_name.lower() in config.NEVER_SPAM_INBOXES:
        bypass_reason = f"never_spam_inbox({inbox_name!r})"
        print(f"[spam] bypass: {bypass_reason} — treating as legitimate")

    # Sender history lookup (used as tiebreaker even when classifier runs).
    if bypass_reason is None and contact_id:
        try:
            prior = await chatwoot.get_contact_conversations(contact_id)
            for c in prior:
                if c.get("id") == conv_id:
                    continue
                labels = {(l or "").lower() for l in (c.get("labels") or [])}
                if "spam" not in labels:
                    sender_history_count += 1
        except Exception as e:
            print(f"[spam] sender-history lookup failed for contact {contact_id}: {e}")

    # One Langfuse span for THIS message; every classifier call below nests
    # under it, and the span nests under the conversation trace (conv_id).
    # `_lf` is a {trace_id, parent_observation_id} dict splatted into each call
    # (empty dict = untraced, so a tracing hiccup never blocks classification).
    _lf = tracing.message_parent(conv_id, data.get("id"), event="message_created")

    if bypass_reason is None:
        result = await classifier.classify_email_type(
            content, sender_email=sender_email, subject=real_subject, lf_parent=_lf
        )
        email_category         = result["label"]
        classifier_conf        = result["confidence"]
        escalation_signal      = result["escalation_signal"]
        escalation_reason_text = result["escalation_reason"]
        print(f"[spam] classifier verdict for conv {conv_id}: "
              f"category={email_category!r} confidence={classifier_conf} "
              f"signal={escalation_signal!r} "
              f"(sender_prior_non_spam={sender_history_count})")

        # Tiebreaker: low-confidence spam from a known sender → promotional.
        if (
            email_category == "spam"
            and classifier_conf < config.SPAM_CONFIDENCE_THRESHOLD
            and sender_history_count >= config.WHITELIST_MIN_PRIOR_CONVERSATIONS
        ):
            print(f"[spam] downgrading low-confidence spam → promotional "
                  f"(known sender with {sender_history_count} prior non-spam)")
            email_category = "promotional"

    # Apply category label ONLY for non-legitimate categories. "legitimate"
    # is the common case and labelling every real customer conversation
    # would pollute the sidebar's Labels group (the sidebar filters
    # CLASSIFIER_MANAGED_LABELS = ['spam','promotional','automated'] OUT of
    # the generic group — 'legitimate' is intentionally not in that list).
    if email_category != "legitimate":
        try:
            await chatwoot.add_label(conv_id, email_category)
        except Exception as e:
            print(f"[spam] add_label({email_category}) failed: {e}")

    # Persist full classification context (audit trail / digest / overrides)
    try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "email_category": email_category,
            "email_classifier": {
                "confidence":           classifier_conf,
                "bypass_reason":        bypass_reason,
                "sender_history_count": sender_history_count,
                "escalation_signal":    escalation_signal,
                "escalation_reason":    escalation_reason_text,
                "classified_at":        _now_iso(),
            },
        })
    except Exception as e:
        print(f"[spam] merge_custom_attributes failed: {e}")

    # ── 13-category classifier (runs ALWAYS, before the spam early-exit) ──
    # Run the Durian routing classifier up-front so a confident business
    # category can OVERRIDE a spam/promotional/automated mislabel from the
    # generic spam classifier ("category wins"). Example: a brand-collab
    # email reads as "promotional" to the spam filter but is really a
    # collaboration_request that must be acknowledged + forwarded.
    category_result = None
    rule = None
    try:
        category_result = await classifier.classify_email_category(
            content, sender_email=sender_email, subject=real_subject, lf_parent=_lf
        )
        rule = category_result.pop("rule", None)
        print(f"[category-v2] conv {conv_id}: category={category_result['category']!r} "
              f"confidence={category_result['confidence']} "
              f"action={category_result['action']!r}")
    except Exception as e:
        print(f"[category-v2] classify ERROR ({type(e).__name__}): {e}")

    category_confident = bool(category_result) and \
        category_result.get("category") != "fallback"

    # Persist the classification NOW — before any gate can return early.
    # Region-gated bulk orders, sector-review cards, and low-confidence
    # decisions all used to exit without storing email_category_v2, which
    # left the CRM sidebar panel blind (no category → no Create Deal button)
    # and the deal-owner resolver without a sector to read.
    if category_result is not None:
        try:
            await chatwoot.merge_custom_attributes(conv_id, {
                "email_category_v2": {
                    **category_result,
                    "classified_at": _now_iso(),
                    "display_name": (rule or {}).get("display_name"),
                },
            })
        except Exception as e:
            print(f"[category-v2] early merge_custom_attributes failed: {e}")

    # AUTO bar: the categoriser only forwards/acts on its own when confidence
    # is at/above CATEGORY_AUTO_CONFIDENCE (default 0.9). Below it — including
    # the fallback band — a human confirms the category first (decision card).
    _conf = (category_result or {}).get("confidence", 0) or 0
    category_auto = (category_confident
                     and _conf >= config.CATEGORY_AUTO_CONFIDENCE)

    # ── Decision A: spam/promotional only stops the flow when the
    # categorizer is ALSO uncertain. A confident business category wins. ──
    if email_category == "spam" and not category_confident:
        auto_snoozed = classifier_conf >= config.SPAM_CONFIDENCE_THRESHOLD
        if auto_snoozed:
            try:
                # Snooze (not resolve) so it lands in the Snoozed tab, not
                # Resolved. snoozed_until=None → reopens on customer reply.
                await chatwoot.toggle_status(conv_id, "snoozed", snoozed_until=None)
                print(f"[spam] conv {conv_id} auto-snoozed (confidence "
                      f"{classifier_conf} ≥ {config.SPAM_CONFIDENCE_THRESHOLD})")
            except Exception as e:
                print(f"[spam] auto-snooze failed: {e}")
        else:
            print(f"[spam] conv {conv_id} labelled 'spam' but kept OPEN "
                  f"(confidence {classifier_conf} < "
                  f"{config.SPAM_CONFIDENCE_THRESHOLD}) — needs human review")
        tracing.event(conv_id, "spam-decision", parent=_lf, output={
            "action": "auto_snoozed" if auto_snoozed else "kept_open_for_review",
            "confidence": classifier_conf,
        })
        return {
            "classified_email_type": "spam",
            "confidence":            classifier_conf,
            "auto_snoozed":          auto_snoozed,
        }

    if email_category == "automated" and not category_confident:
        # Automated / transactional mail with no confident business category —
        # file as General Information and auto-resolve so it stays out of the
        # open queue (a customer reply re-opens the thread).
        print(f"[spam] conv {conv_id} labelled 'automated' → General Information + auto-resolve")
        try:
            await chatwoot.add_label(conv_id, "general-information")
            await chatwoot.toggle_status(conv_id, "resolved")
        except Exception as e:
            print(f"[spam] automated auto-file/resolve failed: {e}")
        tracing.event(conv_id, "email-type-decision", parent=_lf, output={
            "label": "automated", "action": "auto_filed_resolved",
        })
        return {"classified_email_type": "automated", "auto_handled": True, "resolved": True}

    if email_category == "promotional" and not category_confident:
        print(f"[spam] conv {conv_id} labelled 'promotional', keeping in open queue")
        tracing.event(conv_id, "email-type-decision", parent=_lf, output={
            "label": "promotional", "action": "labelled_kept_in_queue",
        })
        return {"classified_email_type": "promotional", "auto_handled": True}

    if email_category in ("spam", "promotional", "automated") and category_confident:
        print(f"[category-v2] '{email_category}' label overridden — confident "
              f"category {category_result['category']!r} wins; proceeding with "
              f"acknowledge/forward")
        tracing.event(conv_id, "category-override", parent=_lf, output={
            "spam_label": email_category,
            "winning_category": category_result["category"],
            "action": "category_wins_proceeding",
        })

    # ── Human-in-the-loop gate ───────────────────────────────────────────
    # Classifier ran but isn't confident enough to auto-act → DON'T forward.
    # Post a Category decision card (AI's best guess + alternatives + a
    # dropdown of all categories) and wait for an agent to confirm. Dry-run
    # keeps its preview behaviour; social DMs are handled elsewhere.
    if (category_result is not None and not category_auto
            and not _PHASE_2_DRY_RUN and not is_social):
        tracing.event(conv_id, "category-decision-card", parent=_lf, output={
            "action": "posted_for_agent_confirmation",
            "suggested": category_result.get("category"),
            "confidence": category_result.get("confidence"),
            "reason": "confidence below auto bar",
        })
        return await _post_category_decision(conv_id, category_result)

    # ── Bulk-order sector gate ───────────────────────────────────────────
    # The category is confident, but bulk orders ALSO need the buyer sector
    # (government vs private) to pick the handler. If the sector is uncertain,
    # don't auto-forward to a guessed handler — flag it for an agent to confirm.
    if (category_result is not None and category_auto
            and category_result.get("category") == "project_bulk_order"
            and (category_result.get("rule") or {}).get("sector_routing")
            and not _PHASE_2_DRY_RUN and not is_social):
        if float(category_result.get("sector_confidence") or 0) < config.BULK_SECTOR_AUTO_CONFIDENCE:
            tracing.event(conv_id, "bulk-sector-review-card", parent=_lf, output={
                "action": "posted_for_agent_confirmation",
                "sector": category_result.get("sector"),
                "sector_confidence": category_result.get("sector_confidence"),
            })
            return await _post_category_decision(conv_id, category_result,
                                                 sector_review_only=True)

    # ── Bulk-order region ────────────────────────────────────────────────
    # NO "region needs an agent decision" card. Bulk orders no longer forward
    # (suppress_forward), and the deal OWNER is resolved at Create Deal time by
    # _resolve_deal_owner, whose fallback chain ALWAYS routes: private state →
    # govt city/state (round-robin) → Saharsh catch-all ("balance states"). So an
    # "uncertain" region is never actually unroutable — carding it wrongly held
    # resolvable enquiries (Kolkata / West Bengal private, Amethi / UP govt) and
    # even skipped the deal-details capture. Let a region-uncertain bulk order
    # flow through phase 2 exactly like a matched one (deal-details gate captures
    # phone + city → deal-ready); the agent creates the deal and the owner is
    # tagged automatically. (_post_bulk_region_review is kept but now unused.)

    # ── Categorizer ACTION (acknowledge + forward) + agent note ──────────
    # Phase 2A (PHASE_2_DRY_RUN=true): render a preview note, send nothing.
    # Phase 2B (PHASE_2_DRY_RUN=false): actually acknowledge + forward.
    if category_result is not None:
      try:
        # (email_category_v2 is persisted earlier, right after classification,
        # so gate-exited conversations keep their category too.)

        # Loop guard: if this conv has already been Phase-2-handled, do
        # NOT execute actions again. Reply lands as a new incoming
        # message → webhook fires → we'd otherwise re-forward / re-ack
        # the customer in a tight loop. The phase2_handled_at flag is
        # the canonical "bot is done with this conv, hand back to human"
        # marker. (Categorizer still ran so the audit row stays useful;
        # only the action layer is suppressed.)
        existing_phase2 = (
            (await chatwoot.get_conversation(conv_id)).get("custom_attributes") or {}
        ).get("phase2_handled_at") if not _PHASE_2_DRY_RUN else None

        # Phase 2A (dry-run) preview OR Phase 2B (real send) actions —
        # mutually exclusive based on the PHASE_2_DRY_RUN env flag.
        action_section: list[str] = []
        if is_social:
            # Social DM: classify + label + team only. No customer
            # acknowledgment, no auto-forwarding (email-only features).
            print(f"[category-v2] conv {conv_id} is social — classify + label "
                  f"only (no acknowledgment / forward)")
        elif _PHASE_2_DRY_RUN:
            try:
                action_section = _render_phase2_dry_run_preview(
                    category_result   = category_result,
                    rule              = rule,
                    sender_name       = sender.get("name") or "",
                    sender_email      = sender_email,
                    original_subject  = real_subject or "",
                )
            except Exception as e:
                print(f"[category-v2] dry-run render failed: {e}")
        elif existing_phase2:
            print(f"[phase2b] conv {conv_id} already handled at "
                  f"{existing_phase2} — skipping action layer (loop guard)")
            action_section = [
                "🔁 _Phase 2 actions already executed for this conversation; "
                "skipping re-send (loop guard)._"
            ]
        else:
            try:
                action_section = await _phase2_execute_actions(
                    conv_id           = conv_id,
                    category_result   = category_result,
                    rule              = rule,
                    sender_name       = sender.get("name") or "",
                    sender_email      = sender_email,
                    original_content  = content,
                    original_subject  = real_subject or "",
                    email_category    = email_category,
                )
            except Exception as e:
                print(f"[phase2b] action layer failed: {e}")
                action_section = [f"⚠️ Phase 2 action layer error: `{e}`"]

        # Compose the agent-facing note. Professional, jargon-free: the
        # category, a one-line reason, and what was done. Internal terms
        # (confidence score, "observe-only", "Phase 2") are kept out — a
        # low-confidence classification is flagged in plain language
        # instead, since that's the only case an agent needs to act on.
        display = (rule or {}).get("display_name") or category_result["category"]
        note_lines = [f"🗂️ **Auto-classified as: {display}**"]
        if category_result.get("category") == "fallback":
            note_lines.append(
                "_The category wasn't clear — please review and route manually._"
            )
        if category_result.get("reason"):
            note_lines.append(category_result["reason"])
        if action_section:
            note_lines.append("")
            note_lines.extend(action_section)

        try:
            await chatwoot.post_private_note(conv_id, "\n".join(note_lines))
        except Exception as e:
            print(f"[category-v2] post_private_note failed: {e}")

        # ── Category label ──────────────────────────────────────────
        cat_label = category_result["category"].replace("_", "-")
        try:
            await chatwoot.add_label(conv_id, cat_label)
        except Exception as e:
            print(f"[category-v2] add_label({cat_label}) failed for conv {conv_id}: {e}")

        # ── Team assignment (categorizer-driven) ─────────────────────
        # When the YAML rule carries a team_id, assign the conversation
        # to that team. Replaces the legacy four-team escalation_signal
        # routing for emails the new categorizer handles — every
        # category has its own dedicated team on the post-reorg prod
        # Chatwoot. Dry-run is exempt (we don't mutate prod state in
        # observe-mode) and so is fallback (no rule to read team_id
        # from; falls through to the legacy routing below).
        rule_team_id = (rule or {}).get("team_id") if rule else None
        if (rule_team_id and not _PHASE_2_DRY_RUN
            and category_result.get("category") != "fallback"):
            try:
                await chatwoot.assign_team(conv_id, int(rule_team_id))
                print(f"[category-v2] conv {conv_id} assigned to team "
                      f"{rule_team_id} ({category_result['category']})")
            except Exception as e:
                print(f"[category-v2] assign_team({rule_team_id}) failed "
                      f"for conv {conv_id}: {e}")
      except Exception as e:
        # Never let the categorizer break the rest of the flow.
        print(f"[category-v2] ERROR ({type(e).__name__}): {e} — continuing")

    # ── Decision B: handling for confident categories ────────────────────
    # The categorizer block above already did the right thing for a
    # confident category:
    #   • forward category   → acknowledged + forwarded + team-assigned
    #   • in-channel category → team-assigned (conversation stays in
    #                          hello@ for the matched team to handle)
    # Per ops guidance no Zoho ticket is ever created for an auto-handled
    # email. So a confident category short-circuits here — skip both the
    # legacy four-team escalation_signal routing AND the Zoho escalation
    # path below. Only the fallback (uncertain) branch falls through to
    # the legacy logic as a safety net.
    if category_confident:
        return {
            "classified":  category_result["category"],
            "action":      category_result["action"],
            "handled_by":  "categorizer",
        }

    # ── Fall through for any uncertain (fallback) email. ──────────────
    # The new categorizer already posted a "fallback" note telling the
    # agent to route manually. Skip the legacy 4-team classifier so we
    # don't assign a stale team (the old IDs no longer match the
    # post-reorg 13-team setup). Zoho escalation still fires below if
    # priority/signal warrants it.
    if category_result and category_result.get("category") == "fallback":
        print(f"[classify] conv {conv_id} is fallback — skipping legacy "
              f"team assignment (agent will triage manually)")
        return {"classified": "fallback", "assigned": False,
                "handled_by": "categorizer_fallback"}

    # Social DMs never run the legacy email team-routing / Zoho escalation —
    # the DM bot owns them. This only matters if the categorizer errored
    # (category_result is None) and we'd otherwise fall through here.
    if is_social:
        print(f"[classify] conv {conv_id} is social — skipping legacy "
              f"email routing / Zoho (handled by DM bot)")
        return {"ignored": True, "reason": "social_no_legacy_routing"}

    # Team routing.
    #
    # We ran TWO LLM calls historically: classify_email_type() (which returns
    # label/confidence/escalation_signal/reason) AND classify() (single-word
    # team). They were independent, and they could disagree — a legal notice
    # was correctly flagged as escalation_signal=legal_or_compliance (Zoho
    # ticket labeled Legal) but the standalone team classifier looked at the
    # body's mention of "professional models" and "personality rights" and
    # misrouted the conversation to HR. The Chatwoot UI then showed "Assigned
    # to HR" alongside a "Zoho ticket created (auto-routed: Legal)" private
    # note for the same message. Confusing and incorrect.
    #
    # Fix: when the email-type classifier returned a STRONG signal
    # (anything other than "none"), let that signal pick the team via
    # classifier.ESCALATION_SIGNAL_TEAM. Skip the generic classifier
    # entirely. Routing and ticket labeling now share one decision and
    # can never drift apart. Bonus: one fewer LLM call per signal-bearing
    # message.
    #
    # Only fall back to classify() when signal == "none" — i.e. an
    # ordinary message with no specific domain bucket. That's the case
    # the generic classifier is designed for.
    signal_team = classifier.ESCALATION_SIGNAL_TEAM.get(escalation_signal)
    if signal_team:
        team_key = signal_team
        team_routing_source = f"signal_{escalation_signal}"
        print(f"[classify] conv {conv_id} routed by escalation signal "
              f"{escalation_signal!r} → team={team_key} "
              f"(skipping generic classifier — keeps routing in sync with "
              f"Zoho ticket team)")
    else:
        print(f"[classify] classifying conv={conv_id} inbox={inbox_name!r} "
              f"content={content[:60]!r} (no escalation signal — using "
              f"generic team classifier)")
        team_key = await classifier.classify(content, inbox_name, lf_parent=_lf)
        team_routing_source = "generic_classifier"
    team_id  = config.TEAM_IDS.get(team_key)
    print(f"[classify] → team={team_key} id={team_id} source={team_routing_source}")

    if not team_id:
        print(f"[classify] no TEAM_ID configured for '{team_key}' — skipping assignment")
        return {"classified": team_key, "assigned": False}

    try:
        result = await chatwoot.assign_team(conv_id, team_id)
        print(f"[classify] assigned OK: {result}")
    except Exception as e:
        print(f"[classify] ERROR assigning team {team_key} ({team_id}) "
              f"to conv {conv_id}: {e}")
        return {"classified": team_key, "assigned": False, "error": str(e)}

    # Option-D Zoho-escalation decision. Inputs are now ALL data-driven:
    # team-routing result, agent-set priority, and the classifier's
    # structured escalation_signal/reason — no keyword regex in sight.
    priority = conv.get("priority")
    should_escalate, escalation_label = _should_create_zoho_ticket(
        team_key, priority,
        escalation_signal=escalation_signal,
        escalation_reason=escalation_reason_text,
    )
    print(f"[zoho] escalation check for conv {conv_id}: "
          f"escalate={should_escalate} reason={escalation_label}")

    zoho_ticket = None
    pending_decision = None
    if should_escalate:
        # Dedup-aware ticket creation (pause if the contact already has
        # open tickets, else create). Same helper the categorizer path
        # uses for complaint/legal.
        zoho_ticket, pending_decision = await _create_or_pause_zoho_ticket(
            conv_id, data, sender_email, escalation_label=escalation_label
        )

    return {
        "classified":            team_key,
        "assigned_team_id":      team_id,
        "team_routing_source":   team_routing_source,
        "zoho_ticket_id":        zoho_ticket,
        "escalation_reason":     escalation_label if should_escalate else None,
        "pending_ticket_choice": pending_decision,
    }


# ── Social DM handoff → template-suggestion card ──────────────────────────
# Chatwoot inbox channel_type → our template-channel prefix. Instagram and
# Facebook share Durian's `social_*` templates (the sheet is "FB & IG DM").
# Reviews aren't here — they're handled by the reviews poller.
TEMPLATE_CHANNEL_FOR_INBOX_TYPE = {
    # Each platform is its own template channel so the AI's candidate pool is
    # platform-specific (instagram_* / facebook_*), then split DM vs comment by
    # surface inside review_reply.draft.
    "Channel::Instagram":    "instagram",
    "Channel::FacebookPage": "facebook",
    "Channel::Whatsapp":     "whatsapp",
}


def _latest_incoming(messages: list) -> str:
    """The customer's most recent text message — the context that triggered
    the handoff (e.g. the complaint the DM bot couldn't resolve)."""
    for m in reversed(messages or []):
        if m.get("message_type") in (0, "incoming") and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


def _recent_incoming(messages: list, n: int = 3) -> str:
    """The customer's last `n` text messages, oldest first — the drafter
    replies to the LAST one but needs the earlier ones as context (customers
    split one thought across messages: "Can we connect on call?" … "And may
    I have your catalogue")."""
    texts = [m["content"].strip() for m in messages or []
             if m.get("message_type") in (0, "incoming")
             and (m.get("content") or "").strip()]
    return "\n".join(texts[-n:])


# Bare greetings / filler the client does NOT engage with on public post
# comments. Praise ("beautiful sofa", "love it") is deliberately NOT here — it
# still gets the AI's warm reply. Roman + a few Devanagari forms.
_LOW_VALUE_COMMENT_PHRASES = {
    "good morning", "good afternoon", "good evening", "good night", "good day",
    "gm", "gn", "gud mrng", "hi", "hii", "hiii", "hello", "helo", "hey", "yo",
    "hola", "namaste", "namaskar", "namastey", "jai shri krishna",
    "jai shree krishna", "jai shri krishn", "jsk", "radhe radhe", "ram ram",
    "jai mata di", "jai hind", "jai shri ram", "jai shree ram", "ya fir",
    "ok", "okay", "k", "hmm", "hmmm", "done", "thanks", "thank you", "thankyou",
    "welcome", "jsr", "jai jinendra",
    # Devanagari
    "जय श्री कृष्ण", "राधे राधे", "नमस्ते", "नमस्कार", "सुप्रभात", "शुभ प्रभात",
    "जय हिंद", "राम राम", "जय श्री राम",
}
# Emoji / pictographs / symbols / regional-indicator (flag) / variation selectors.
_EMOJI_SYMBOL_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F\U00002190-\U000021FF"
    "\U00002460-\U000024FF‍♀♂❤]")


def _is_low_value_comment(text: str) -> bool:
    """Cheap, no-AI guardrail: True when a public comment isn't worth a reply —
    emoji/symbol-only, empty, or a bare greeting/filler phrase. Anything with a
    real word, question, or praise returns False (falls through to the AI)."""
    raw = (text or "").strip()
    if not raw:
        return True
    # Meaningful tokens after stripping emojis/symbols: latin or devanagari.
    words = re.findall(r"[0-9a-zA-Zऀ-ॿ]+", _EMOJI_SYMBOL_RE.sub("", raw))
    if not words:
        return True  # emoji / symbol only
    norm = " ".join(w.lower() for w in words)
    if norm in _LOW_VALUE_COMMENT_PHRASES:
        return True
    # Short (<=3 tokens) and EVERY token is a greeting/filler word.
    filler = {w for p in _LOW_VALUE_COMMENT_PHRASES for w in p.lower().split()}
    if len(words) <= 3 and all(w.lower() in filler for w in words):
        return True
    return False


async def handle_template_suggest(conv: dict, channel: str,
                                  surface: str = "") -> dict:
    """Post a Durian-template AI reply suggestion as a private note for a
    social DM (or, with surface="comment", a public post comment). The agent
    gets the best-matching Durian template (edit / regenerate / send) instead
    of a blank reply box.

    Dedup rule: post a fresh card on every customer message that lands AFTER
    the team's last reply (or on the very first message). If a previous card
    is still pending (the team hasn't sent/cancelled it yet), do NOT stack
    another — that handles a customer firing off 2-3 messages in quick
    succession. Once the team sends or cancels, the next customer message
    gets a brand-new card."""
    conv_id = conv.get("id")
    if not conv_id:
        return {"ignored": True, "reason": "no_conversation_id"}

    contact_name = ((conv.get("meta") or {}).get("sender") or {}).get("name") \
        or "Customer"
    # Fetch ALL messages including private notes — get_conversation_messages
    # strips private ones (cards are private), which would defeat the dedup
    # check below. Inline fetch with no filter.
    all_messages = await chatwoot.get_conversation_messages_raw(conv_id)
    # Last few customer messages, not just the latest: customers split one
    # thought across messages ("Can we connect on call?" … "And may I have
    # your catalogue") and the drafter needs the whole thought.
    message = _recent_incoming(all_messages)
    if not message:
        return {"ignored": True, "reason": "no_customer_message"}

    # Guardrail (no AI): skip low-value PUBLIC comments — emoji-only, a bare
    # greeting ('good morning', 'jai shri krishna', 'ya fir'), or filler the
    # client does not engage with. No AI call, no card, no agent-needed. Real
    # questions / praise fall through to the AI as before. DMs are unaffected.
    if surface == "comment" and _is_low_value_comment(message):
        print(f"[template-suggest] conv {conv_id} — low-value comment, no reply (no AI)")
        return {"ignored": True, "reason": "low_value_comment"}

    # Social DM/comment handoff — flag it for this channel's agent-needed section.
    await _flag_agent_needed(conv_id, channel)

    # id of that same latest incoming message, for the Langfuse message span.
    msg_id = next((m.get("id") for m in reversed(all_messages)
                   if m.get("message_type") in (0, "incoming")
                   and (m.get("content") or "").strip()), None)

    # If the latest outgoing message is itself a pending suggestion card, skip
    # — the team hasn't actioned it yet. The Send flow deletes the card and
    # creates a public reply, the Cancel flow deletes it outright, so once
    # actioned the next customer message correctly gets a fresh card.
    last_out = next(
        (m for m in reversed(all_messages) if m.get("message_type") in (1, "outgoing")),
        None,
    )
    if last_out and (last_out.get("content_attributes") or {}).get("type") == "ai_review_suggestion":
        print(f"[template-suggest] conv {conv_id} — card already pending, skipping")
        return {"ignored": True, "reason": "card_already_pending"}

    try:
        _lf = tracing.message_parent(conv_id, msg_id, name="template-suggest",
                                     channel=channel)
        drafted = await review_reply.draft(
            channel=channel, message=message, contact_name=contact_name,
            lf_parent=_lf, surface=surface,
        )
    except Exception as e:
        print(f"[template-suggest] draft failed for conv {conv_id}: {e}")
        return {"ignored": True, "reason": "draft_failed"}

    reply, action = drafted["reply"], drafted["action"]
    if not reply:
        return {"ignored": True, "reason": "no_draft"}

    try:
        await chatwoot.create_message(
            conv_id, reply, message_type="outgoing", private=True,
            content_attributes={"type": "ai_review_suggestion",
                                "suggestion": reply, "channel": channel,
                                "surface": surface,
                                "ai_trace": drafted["trace"]},
        )
    except Exception as e:
        print(f"[template-suggest] post failed for conv {conv_id}: {e}")
        return {"ignored": True, "reason": "post_failed"}

    print(f"[template-suggest] {channel} card posted on conv {conv_id} ({action})")
    return {"posted": True, "channel": channel, "action": action}


# ── Handler: agent reply on a review → post to Google ─────────────────────
async def handle_review_reply(data: dict) -> dict:
    inbox_id = (data.get("inbox") or {}).get("id")
    if not config.REVIEWS_INBOX_ID or inbox_id != config.REVIEWS_INBOX_ID:
        return {"ignored": True, "reason": "not_reviews_inbox"}
    if data.get("private"):
        return {"ignored": True, "reason": "private_note"}
    if (data.get("content_attributes") or {}).get("source") == reviews_poller.AUTO_MARKER["source"]:
        return {"ignored": True, "reason": "auto_reply_already_posted"}

    conv = data.get("conversation") or {}
    conv_id = conv.get("id")
    content = (data.get("content") or "").strip()
    if not conv_id or not content:
        return {"ignored": True, "reason": "no_conv_or_content"}

    reply_path = reviews_state.reply_path_for_conversation(conv_id) \
        or (conv.get("custom_attributes") or {}).get("review_path")
    if not reply_path:
        return {"ignored": True, "reason": "no_review_path"}

    try:
        await gr.post_reply(reply_path, content)
        # Segregate manual replies from auto ones + tag the replying agent
        # (covers direct replies too, not just template-card approvals) so the
        # reviews "replied by <agent>" filter works. Uses a name-slug label
        # (replied-by-aditya) instead of the raw id (replied-by-1) so the
        # chip that shows up on the conversation card is readable.
        manual_labels = [reviews_poller.LBL_REPLIED, reviews_poller.LBL_MANUALLY_REPLIED]
        sender = data.get("sender") or {}
        slug = reviews_poller.agent_name_slug(
            sender.get("available_name") or sender.get("name") or "",
            sender.get("email") or "",
            sender.get("id"),
        )
        if slug:
            manual_labels.append(f"replied-by-{slug}")
        await reviews_poller.tag_reply_status(
            conv_id, *manual_labels, remove=(reviews_poller.LBL_UNREPLIED,))
        print(f"[reviews] posted human reply for conv {conv_id}")
        return {"posted": True, "conversation_id": conv_id}
    except Exception as e:
        print(f"[reviews] ERROR posting human reply for conv {conv_id}: {e}")
        return {"posted": False, "error": str(e)}


# ── Dispatcher ────────────────────────────────────────────────────────────
HANDLERS = {
    "conversation_status_changed": handle_status_changed,
    "conversation_updated":        handle_conversation_updated,
    "message_created":             handle_message_created,
}


@app.post("/chatwoot/webhook")
async def chatwoot_webhook(
    request: Request,
    x_chatwoot_signature: Optional[str] = Header(None),
    x_chatwoot_timestamp: Optional[str] = Header(None),
):
    raw = await request.body()
    _verify_signature(x_chatwoot_signature, x_chatwoot_timestamp, raw)
    data = await request.json()

    event   = data.get("event")
    handler = HANDLERS.get(event)
    if not handler:
        return {"ignored": True, "reason": f"event={event}"}

    return await handler(data)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/reviews/regenerate")
async def reviews_regenerate(request: Request):
    """Called by the Chatwoot Rails proxy when an agent clicks "Regenerate"
    on the AI suggestion card. Re-drafts a fresh Durian template reply and
    returns it (the card swaps in the new text). URL is kept as
    /reviews/regenerate for backward compat with the Rails proxy; works for
    every channel via the `channel` body field.

    Body: { "conversation_id": int, "channel": "review"|"whatsapp"|"instagram"|"facebook" }
    Returns: { "suggestion": str, "action": "auto"|"handoff" }
    """
    body = await request.json()
    conv_id = body.get("conversation_id")
    channel = (body.get("channel") or "review").strip()
    if not conv_id:
        return {"error": "missing conversation_id"}

    try:
        conv = await chatwoot.get_conversation(conv_id)
    except Exception as e:
        return {"error": f"could not load conversation: {e}"}

    contact_name = ((conv.get("meta") or {}).get("sender") or {}).get("name") \
        or "Customer"
    add = conv.get("additional_attributes") or {}
    cust = conv.get("custom_attributes") or {}

    if channel == "review":
        # The poller stashed the raw review payload on additional_attributes
        # so regenerate doesn't need to re-parse the formatted message body.
        # Agent-triggered re-draft (no new inbound message) → conversation-level
        # span, no message_id.
        # If the review was edited (poller stashes review_edited_* on
        # custom_attributes), re-draft from the EDITED text/rating — the
        # original in additional_attributes can't be updated via the API.
        _lf = tracing.message_parent(conv_id, name="review-regenerate")
        drafted = await review_reply.draft(
            channel="review",
            message=cust.get("review_edited_comment") or add.get("review_comment") or "",
            contact_name=add.get("reviewer") or contact_name,
            stars=cust.get("review_edited_stars") or add.get("stars") or 0,
            location=add.get("location") or "",
            lf_parent=_lf,
        )
    else:
        # For social DMs/comments, the customer's recent messages are the
        # context (comment conversations regenerate against the comment pool).
        messages = await chatwoot.get_conversation_messages(conv_id)
        _lf = tracing.message_parent(conv_id, name="template-regenerate",
                                     channel=channel)
        drafted = await review_reply.draft(
            channel=channel,
            message=_recent_incoming(messages),
            contact_name=contact_name,
            lf_parent=_lf,
            surface="comment" if _is_comment_conversation(conv) else "",
        )
    return {"suggestion": drafted["reply"], "action": drafted["action"],
            "ai_trace": drafted["trace"]}


def _escalation_transcript(messages: list) -> str:
    """Readable Customer/Team transcript of a review conversation for the
    'full conversation history' escalation option. Skips private notes."""
    lines = []
    for m in messages or []:
        if m.get("private"):
            continue
        mtype = m.get("message_type")
        who = "Customer" if mtype in (0, "incoming") else (
            "Team" if mtype in (1, "outgoing") else None)
        content = (m.get("content") or "").strip()
        if who and content:
            lines.append(f"{who}: {content}")
    return "\n\n".join(lines)


@app.post("/reviews/escalate")
async def reviews_escalate(request: Request):
    """Send a bad Google review to the team by email (the "Escalate to team"
    button on a review conversation). The reviews inbox is an API channel and
    can't send email, so the escalation is sent through the email inbox
    (REVIEW_ESCALATION_INBOX_ID). An audit note is posted back on the review.

    Body: {conversation_id, to_emails, cc_emails?, subject, body,
           include_history?, agent?}
    """
    body_json    = await request.json()
    conv_id      = body_json.get("conversation_id")
    to_emails    = (body_json.get("to_emails") or "").strip()
    cc_emails    = (body_json.get("cc_emails") or "").strip()
    subject      = (body_json.get("subject") or "Negative Feedback Received on Google").strip()
    email_body   = (body_json.get("body") or "").strip()
    include_hist = bool(body_json.get("include_history"))
    agent        = (body_json.get("agent") or "").strip()

    if not conv_id or not to_emails or not email_body:
        raise HTTPException(400, "conversation_id, to_emails and body are required")
    if not config.REVIEW_ESCALATION_INBOX_ID:
        raise HTTPException(503, "escalation email inbox not configured "
                                 "(REVIEW_ESCALATION_INBOX_ID)")

    # Optionally append the full conversation transcript.
    if include_hist:
        try:
            msgs = await chatwoot.get_conversation_messages_raw(int(conv_id))
            transcript = _escalation_transcript(msgs)
            if transcript:
                email_body += ("\n\n----------------------------------------\n"
                               "Conversation history:\n\n" + transcript)
        except Exception as e:
            print(f"[review-escalate] transcript fetch failed conv {conv_id}: {e}")

    # Send through the email inbox: a fresh conversation whose mail_subject
    # drives the outgoing subject, sent to the agent-entered recipients.
    try:
        # ONE fixed, reusable escalation contact — NOT keyed on the recipient's
        # email. Keying on the recipient 422s whenever that address already
        # exists as a contact (Chatwoot enforces unique emails). The real
        # recipients ride on to_emails below; this contact is just the
        # conversation's nominal record, created once and reused thereafter.
        contact_id, source_id = await chatwoot.create_contact(
            name="Durian Review Escalations",
            identifier="durian-review-escalation",
            inbox_id=config.REVIEW_ESCALATION_INBOX_ID,
            email="review-escalations@durian.in",
        )
        esc_conv = await chatwoot.create_conversation(
            source_id=source_id or f"esc_{conv_id}",
            inbox_id=config.REVIEW_ESCALATION_INBOX_ID,
            contact_id=contact_id,
            additional_attributes={"mail_subject": subject},
        )
        await chatwoot.send_outgoing_message(
            esc_conv, email_body, to_emails=to_emails,
            cc_emails=cc_emails or None,
        )
    except Exception as e:
        print(f"[review-escalate] send failed conv {conv_id}: {e}")
        raise HTTPException(502, f"could not send escalation email: {e}")

    # Audit trail on the original review.
    try:
        who = f" by {agent}" if agent else ""
        cc_note = f" (cc {cc_emails})" if cc_emails else ""
        await chatwoot.post_private_note(
            int(conv_id), f"📧 Review escalated to {to_emails}{cc_note}{who}.")
    except Exception as e:
        print(f"[review-escalate] audit note failed conv {conv_id}: {e}")

    print(f"[review-escalate] conv {conv_id} → {to_emails} (history={include_hist})")
    return {"sent": True, "to": to_emails, "escalation_conversation_id": esc_conv}


@app.post("/chatwoot/resolve-ticket-decision")
async def chatwoot_resolve_ticket_decision(request: Request):
    """Called by the Chatwoot Rails proxy when an agent clicks
    [Approve / Create new], [Attach to #N], or [Reject] in the Pending Ticket
    Decision panel.

    Body: {
      "conversation_id": int,
      "choice":          "use_existing" | "create_new" | "reject",
      "target_ticket_id": str  // required when choice == "use_existing"
    }
    """
    body = await request.json()
    conv_id          = body.get("conversation_id")
    choice           = body.get("choice")
    target_ticket_id = body.get("target_ticket_id")
    if not conv_id or choice not in ("use_existing", "create_new", "reject"):
        raise HTTPException(400, "missing conversation_id or invalid choice")
    if choice == "use_existing" and not target_ticket_id:
        raise HTTPException(400, "target_ticket_id required when choice=use_existing")

    try:
        result = await _resolve_ticket_decision(
            conv_id=int(conv_id),
            choice=choice,
            target_ticket_id=target_ticket_id,
        )
    except Exception as e:
        print(f"[zoho-dedup] resolve endpoint failed for conv {conv_id}: "
              f"{type(e).__name__}: {e}")
        raise HTTPException(500, f"resolve failed: {e}")
    return result


@app.post("/chatwoot/resolve-category-decision")
async def chatwoot_resolve_category_decision(request: Request):
    """Called by the Chatwoot Rails proxy when an agent confirms a category in
    the Category Decision panel. Runs the real forward/route for that category.

    Body: { "conversation_id": int, "category": "<category_key>" }
    """
    body       = await request.json()
    conv_id    = body.get("conversation_id")
    category   = body.get("category")
    sector     = body.get("sector")      # bulk orders only: government | private
    agent_name = body.get("agent_name")  # injected by the Rails proxy (Current.user)
    if not conv_id or not category:
        raise HTTPException(400, "missing conversation_id or category")
    try:
        return await _resolve_category_decision(int(conv_id), category, sector,
                                                agent_name=agent_name or "")
    except Exception as e:
        print(f"[category-decision] resolve endpoint failed for conv {conv_id}: "
              f"{type(e).__name__}: {e}")
        raise HTTPException(500, f"resolve failed: {e}")


# ── CRM Lead / Deal creation endpoints ────────────────────────────────────
# Called by the Chatwoot Rails proxy when the agent clicks "Create Lead" or
# "Create Deal" in the CRM sidebar panel. Idempotent — a re-click on an
# already-linked conversation just returns the existing id.

def _conv_sender(conv: dict) -> tuple[str, str]:
    """Pull (name, email) from a Chatwoot conversation dict."""
    sender = (conv.get("meta") or {}).get("sender") or {}
    return (sender.get("name") or ""), (sender.get("email") or "")


def _conv_first_incoming_body(messages: list) -> tuple[str, str]:
    """Return (subject, body) from the first incoming message in the thread."""
    for m in messages:
        if m.get("message_type") in (0, "incoming"):
            content = (m.get("content") or "").strip()
            attrs   = (m.get("content_attributes") or {}).get("email") or {}
            subject = attrs.get("subject") or ""
            return subject, content
    return "", ""


async def _ensure_crm_contact(conv_id: int, conv: dict, owner_id: str = "") -> str:
    """Return the crm_contact_id for this conversation, creating one if needed.
    Reuses the crm_contact_id stashed by the auto path (Phase A) so we don't
    create a duplicate. owner_id assigns a newly-created Contact."""
    custom = conv.get("custom_attributes") or {}
    if custom.get("crm_contact_id"):
        return str(custom["crm_contact_id"])
    name, email = _conv_sender(conv)
    if not email:
        raise HTTPException(400, "conversation has no sender email — cannot key a CRM Contact")
    contact_id, created = await zoho_crm.find_or_create_contact(email, name, owner_id=owner_id)
    if not contact_id:
        raise HTTPException(500, "CRM Contact could not be created")
    if created:
        try:
            await chatwoot.merge_custom_attributes(
                conv_id, {"crm_contact_id": contact_id,
                          "crm_contact_url": zoho_crm.contact_url(contact_id)})
        except Exception as e:
            print(f"[crm] merge crm_contact_id failed for conv {conv_id}: {e}")
    return contact_id


# NOTE: there is intentionally NO "create lead" endpoint — the client treats
# Leads and Deals as the same thing, so the only manual CRM action is Deal
# creation (per the deal-qualification flow: Govt → govt owner; otherwise
# location-wise owner; human approval = the agent clicking the button).


def _owner_dict(location: str, entry: dict, vertical: str = "") -> dict:
    return {"configured": True, "location": location,
            "owner_id":    str(entry.get("owner_id") or ""),
            "owner_email": entry.get("owner_email") or "",
            "vertical":    entry.get("business_vertical") or vertical}


def _fallback_owner() -> dict:
    """Central hello@ inbox — used when nothing enquiry-specific resolves.
    The Phase-2 retail matrix is deliberately NOT consulted."""
    fb = (classifier._ROUTING_RULES or {}).get("crm_owner_routing_fallback") or {}
    return {"configured": True, "location": "central",
            "owner_id":    str(fb.get("owner_id") or ""),
            "owner_email": fb.get("owner_email") or "hello@durian.in",
            "vertical":    ""}


def _mentions_doors(body_text: str, subject: str) -> bool:
    """A doors/veneer/plywood/laminate enquiry — even when it classifies as a
    bulk order (e.g. '100 doors') it must route to the doors desks, not the
    furniture bulk owners."""
    t = f"{subject} {body_text}".lower()
    return any(w in t for w in ("door", "veneer", "plywood", "laminate"))


async def _classify_owner_region(mapping_keys: list, body_text: str,
                                 subject: str, sender_email: str, label: str):
    """AI-match the enquiry to one of `mapping_keys`; returns the key or None."""
    if not mapping_keys:
        return None
    try:
        region = await classifier.classify_region(
            body_text, sender_email, subject, region_keys=mapping_keys)
        loc = region.get("region")
        return loc if loc and loc != "other" and loc in mapping_keys else None
    except Exception as e:
        print(f"[crm] {label} region classify failed: {e}")
        return None


async def _resolve_named_owner(mapping: dict, body_text: str, subject: str,
                               sender_email: str, label: str,
                               vertical: str = "") -> dict:
    """Resolve to a single-owner mapping (location → owner). No match →
    central fallback (never the parked retail matrix)."""
    loc = await _classify_owner_region(list(mapping.keys()), body_text, subject,
                                       sender_email, label)
    if not loc:
        return _fallback_owner()
    return _owner_dict(f"{label}:{loc}", mapping[loc], vertical)


async def _resolve_doors_owner(body_text: str, subject: str,
                               sender_email: str) -> dict:
    """Doors desks: Bangalore → bangalore@durian.in, everywhere else →
    rohit.kanoujia@durian.in."""
    doors = (classifier._ROUTING_RULES or {}).get("crm_owner_routing_doors") or {}
    try:
        loc = await classifier.classify_doors_location(body_text, sender_email, subject)
        key = "bangalore" if loc.get("location") == "bangalore" else "other"
    except Exception as e:
        print(f"[crm] doors location classify failed: {e}")
        key = "other"
    entry = doors.get(key) or {}
    return _owner_dict(f"doors-{key}", entry, "Doors") if entry.get("owner_id") \
        else _fallback_owner()


SAHARSH_TERRITORY_KEY = "Delhi / NCR (except Noida) / Haryana / J&K / balance states"

# Deterministic keyword override for Saharsh's OWN territory: Delhi-NCR
# (except Noida proper), Haryana, J&K, Chandigarh. The AI classifier tends
# to fuzzy-match NCR-adjacent cities to Noida and Chandigarh to Punjab —
# these force Saharsh regardless. (Balance STATES are NOT keyworded here:
# a state either appears in crm_owner_routing_govt_bulk_states — and its
# cities round-robin over that state's owners — or it doesn't, and the
# classifier's no-match naturally falls to Saharsh.)
SAHARSH_TERRITORY_KEYWORDS = (
    # NCR (except Noida proper — Noida has its own dedicated owner)
    "gurgaon", "gurugram", "faridabad", "ghaziabad",
    "greater noida", "noida extension",
    # Haryana
    "haryana", "hisar", "karnal", "panipat", "rohtak", "sonipat", "ambala",
    # J&K + Ladakh
    "jammu", "kashmir", "srinagar", "leh", "ladakh",
    # Chandigarh (UT, no explicit owner → Saharsh per client)
    "chandigarh", "panchkula",
)


def _mentions_saharsh_territory(body_text: str, subject: str) -> bool:
    """True when the enquiry clearly mentions a place in Saharsh's own
    territory (NCR-except-Noida / Haryana / J&K / Chandigarh). Used to
    OVERRIDE the AI classifier when it would fuzzy-match the wrong bucket
    (Gurgaon → Noida, Chandigarh → Punjab)."""
    t = f" {(subject or '')} {(body_text or '')} ".lower()
    return any(f" {kw} " in t or f" {kw}." in t or f" {kw}," in t
               for kw in SAHARSH_TERRITORY_KEYWORDS)


# Common-name aliases for the single-city govt/bulk keys, used for the
# deterministic "is this city literally named?" check.
_GOVT_BULK_CITY_ALIASES = {
    "Bhopal":       ("bhopal",),
    "Indore":       ("indore",),
    "Ahmedabad":    ("ahmedabad", "amdavad"),
    "Chennai":      ("chennai", "madras"),
    "Coimbatore":   ("coimbatore", "kovai"),
    "Kolkata":      ("kolkata", "calcutta"),
    "Mumbai":       ("mumbai", "bombay"),
    "Pune":         ("pune", "poona"),
    "Bhubaneshwar": ("bhubaneshwar", "bhubaneswar"),
    "Patna":        ("patna",),
    "Ranchi":       ("ranchi",),
    "Guwahati":     ("guwahati", "gauhati"),
    "Hyderabad":    ("hyderabad",),
    "Noida":        ("noida",),
}


def _key_literally_mentioned(key: str, body_text: str, subject: str) -> bool:
    """True when a single-city govt/bulk key is literally named in the
    enquiry. Enforces: a listed city's owner gets deals for THAT city."""
    t = f" {(subject or '')} {(body_text or '')} ".lower()
    for a in _GOVT_BULK_CITY_ALIASES.get(key, (key.lower(),)):
        if f" {a} " in t or f" {a}." in t or f" {a}," in t or f" {a}'" in t:
            return True
    return False


def _state_owner_pool(state: str) -> tuple[str, list]:
    """All owners covering `state`, flattened across its mapped govt/bulk
    keys in listed order. Returns (pool_key, owners) — e.g. Madhya Pradesh →
    Bhopal's two owners + Indore's one, round-robined as one pool."""
    R = classifier._ROUTING_RULES or {}
    gb = R.get("crm_owner_routing_govt_bulk") or {}
    state_map = R.get("crm_owner_routing_govt_bulk_states") or {}
    owners = []
    for key in state_map.get(state) or []:
        owners.extend(gb.get(key) or [])
    return f"govt_bulk_state:{state}", owners


async def _resolve_govt_bulk_owner(body_text: str, subject: str,
                                   sender_email: str) -> dict:
    """Govt/bulk furniture routing, per the client's rules:

    1. Saharsh's own territory (NCR-except-Noida / Haryana / J&K /
       Chandigarh) → Saharsh. Deterministic keyword override, so Gurgaon
       never fuzzy-matches to Noida nor Chandigarh to Punjab.
    2. A listed city, literally named in the enquiry → that city's owner(s),
       round-robin within the city (Mumbai ×3, Bhopal ×2, Kolkata ×2).
    3. An UNLISTED city in a COVERED state → round-robin across ALL that
       state's owners (Jabalpur → Bhopal's two + Indore's one; Vellore →
       Chennai + Coimbatore; Nagpur → Mumbai's three + Pune). "Why would
       Saharsh handle it if there's an owner close to that city?" — client.
    4. A state with NO owner (Kerala, Goa, HP, NE...) or no location at
       all → Saharsh (balance / catch-all)."""
    R = classifier._ROUTING_RULES or {}
    gb = R.get("crm_owner_routing_govt_bulk") or {}
    state_map = R.get("crm_owner_routing_govt_bulk_states") or {}

    # 1. Saharsh's own territory — deterministic.
    if _mentions_saharsh_territory(body_text, subject):
        key, owners = SAHARSH_TERRITORY_KEY, gb.get(SAHARSH_TERRITORY_KEY) or []
    else:
        # 2. Listed city literally named — deterministic, no AI needed.
        key = next((k for k in gb
                    if k != SAHARSH_TERRITORY_KEY
                    and _key_literally_mentioned(k, body_text, subject)), None)
        if key:
            owners = gb.get(key) or []
        else:
            # 3. AI maps the enquiry to a covered STATE (or the multi-state
            #    'Uttrakhand and Punjab' / 'Rajasthan' keys, which the state
            #    map also routes through).
            state = await _classify_owner_region(
                list(state_map.keys()), body_text, subject, sender_email,
                "govt-bulk-state")
            if state:
                key, owners = _state_owner_pool(state)
            else:
                # 4. Balance states / unknown → Saharsh.
                key, owners = SAHARSH_TERRITORY_KEY, gb.get(SAHARSH_TERRITORY_KEY) or []

    if not owners:
        owners = gb.get(SAHARSH_TERRITORY_KEY) or []
        key = SAHARSH_TERRITORY_KEY
    if not owners:
        return _fallback_owner()
    idx = crm_state.next_index(key, len(owners))
    return _owner_dict(f"{key}#{idx}", owners[idx], "Furniture")


async def _resolve_bulk_sector(cat_v2: dict, body_text: str, subject: str,
                               sender_email: str, sector_override: str):
    """government | private | None(unclear). Precedence: agent override →
    confident stored classification → fresh classify_bulk_sector."""
    sector = (sector_override or "").lower()
    if sector in ("government", "private"):
        return sector
    if float(cat_v2.get("sector_confidence") or 0) >= config.BULK_SECTOR_AUTO_CONFIDENCE:
        s = (cat_v2.get("sector") or "").lower()
        if s in ("government", "private"):
            return s
    try:
        s = await classifier.classify_bulk_sector(body_text, sender_email, subject)
        if float(s.get("confidence") or 0) >= config.BULK_SECTOR_AUTO_CONFIDENCE:
            return (s.get("sector") or "").lower() or None
    except Exception as e:
        print(f"[crm] deal sector classify failed: {e}")
    return None


async def _resolve_deal_owner(custom: dict, body_text: str, subject: str,
                              sender_email: str,
                              sector_override: str = "") -> dict:
    """Owner resolution for DEALS — Phase-1 matrix, routed by ENQUIRY TYPE
    first, then location (the retail location matrix is PARKED):

      Franchise          → franchise desk
      FHC / Home Studio  → home-studio owner (by location)
      Doors (incl. bulk) → doors desks (Bangalore vs rest)
      Bulk furniture     → govt (city/region, round-robin) vs private (4 cities)
      Anything else      → central hello@ fallback (never a retail owner)
    """
    R = classifier._ROUTING_RULES or {}
    cat_v2 = custom.get("email_category_v2") or {}
    category = (cat_v2.get("category") or custom.get("phase2_category") or "")

    # Retail: the retail gate captured the showroom the customer chose → tag its
    # CRM owner on the deal (un-parks the retail matrix for product enquiries).
    retail_owner = custom.get("retail_deal_owner") or {}
    if retail_owner.get("owner_id"):
        return {"configured": True, "location": retail_owner.get("location") or None,
                "owner_id": str(retail_owner["owner_id"]),
                "owner_email": retail_owner.get("crm_email") or "",
                "vertical": "Furniture"}

    # Franchise/dealership → single dedicated desk.
    franchise = R.get("crm_owner_routing_franchise") or {}
    if category == "franchise_dealership" and franchise.get("owner_id"):
        return _owner_dict("franchise", franchise, "Furniture")

    # FHC / Home Studio → home-studio owner by location.
    if category in config.ZOHO_CRM_HOME_STUDIO_CATEGORIES:
        return await _resolve_named_owner(
            R.get("crm_owner_routing_homestudio") or {},
            body_text, subject, sender_email, "homestudio", "FHC")

    # Doors — a retail doors enquiry OR a bulk order that's about doors → the
    # doors desks (never the furniture bulk owners).
    if category == "doors_veneer_plywood" or \
       (category == "project_bulk_order" and _mentions_doors(body_text, subject)):
        return await _resolve_doors_owner(body_text, subject, sender_email)

    # Bulk / project furniture → govt vs private.
    if category == "project_bulk_order":
        sector = await _resolve_bulk_sector(cat_v2, body_text, subject,
                                            sender_email, sector_override)
        if sector is None:
            return {"configured": True, "location": None, "sector_unclear": True,
                    "reason": "buyer type unclear — agent must pick "
                              "government or private"}
        if sector == "government":
            return await _resolve_govt_bulk_owner(body_text, subject, sender_email)
        # Private: try the 4 dedicated private-project cities first
        # (Bangalore / Delhi / Hyderabad / Pune). If the enquiry is NOT in one
        # of those, fall through to the govt/bulk city/state list — the client
        # wants private bulk orders to reach the nearest bulk-owner too, not
        # land on hello@ central. Retail matrix stays parked either way.
        private = R.get("crm_owner_routing_private") or {}
        loc = await _classify_owner_region(list(private.keys()), body_text,
                                           subject, sender_email, "private")
        if loc:
            return _owner_dict(f"private:{loc}", private[loc], "Furniture")
        return await _resolve_govt_bulk_owner(body_text, subject, sender_email)

    # Product / general / existing-order enquiry (and anything else) → central
    # hello@ inbox. The retail location matrix is parked for Phase 2.
    return _fallback_owner()


def _deal_transcript(messages: list, max_msgs: int = 12,
                     max_chars: int = 5000) -> str:
    """Human-readable transcript of the public conversation for the Deal
    description — Customer/Agent prefixed, newest-complete, capped so a long
    thread can't blow past Zoho's field limits."""
    lines = []
    for m in messages:
        if m.get("private"):
            continue
        mtype = m.get("message_type")
        if mtype in (0, "incoming"):
            who = "Customer"
        elif mtype in (1, "outgoing"):
            who = "Agent"
        else:
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{who}: {content[:600]}")
    text = "\n\n".join(lines[-max_msgs:])
    return text[:max_chars]


async def _deal_description(conv_id: int, conv: dict, messages: list,
                            name: str, email: str, subject: str,
                            category_display: str, owner: dict,
                            layout_name: str = "Standard") -> str:
    """Everything a salesperson needs, in the Deal's Description: who the
    customer is (incl. phone when Chatwoot has it), what they asked for
    (AI summary + transcript), how it was qualified (category/sector/
    location), and a link back to the full Chatwoot conversation."""
    sender = (conv.get("meta") or {}).get("sender") or {}
    phone  = sender.get("phone_number") or ""
    sector = "government" if owner.get("location") == "govt" else "private"
    link   = (f"{config.CHATWOOT_PUBLIC_URL.rstrip('/')}"
              f"/app/accounts/{config.CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}")
    parts = [
        f"Category:  {category_display}",
        f"Sector:    {sector}",
        f"Location:  {owner.get('location') or 'n/a'}",
        f"Vertical:  {owner.get('vertical') or 'Furniture'}",
        f"Layout:    {layout_name}",
        f"Store:     {owner.get('owner_email') or 'n/a'}",
        f"From:      {name or email} <{email}>" + (f" · {phone}" if phone else ""),
        f"Subject:   {subject or '(no subject)'}",
        f"Chatwoot:  {link}",
    ]
    # AI summary — same summarizer the Desk tickets use. Best-effort.
    try:
        summary = await summarizer.summarize_conversation(messages) if messages else {}
    except Exception:
        summary = {}
    if summary.get("summary") or summary.get("customer_goal"):
        parts.append("")
        parts.append("--- Summary ---")
        if summary.get("summary"):
            parts.append(f"What happened: {summary['summary']}")
        if summary.get("customer_goal"):
            parts.append(f"Customer wants: {summary['customer_goal']}")
        if summary.get("next_step"):
            parts.append(f"Suggested next step: {summary['next_step']}")
    transcript = _deal_transcript(messages)
    if transcript:
        parts.append("")
        parts.append("--- Conversation ---")
        parts.append(transcript)
    return "\n".join(parts)


@app.post("/chatwoot/crm/create-deal")
async def chatwoot_crm_create_deal(request: Request):
    """Create a CRM Deal linked to the CRM Contact for this conversation.
    Idempotent via crm_deal_id. Body: {conversation_id, agent_name?, sector?}
    — `sector` is the agent's government/private choice when classification
    was ambiguous (the endpoint returns 409 to request it)."""
    if not config.ZOHO_CRM_ENABLED:
        raise HTTPException(503, "CRM not configured")
    body = await request.json()
    conv_id = body.get("conversation_id")
    if not conv_id:
        raise HTTPException(400, "missing conversation_id")
    try:
        conv     = await chatwoot.get_conversation(int(conv_id))
        messages = await chatwoot.get_conversation_messages(int(conv_id))
    except Exception as e:
        raise HTTPException(500, f"could not read conversation: {e}")

    custom = conv.get("custom_attributes") or {}
    if custom.get("crm_deal_id"):
        return {"deal_id": custom["crm_deal_id"], "created": False,
                "url": zoho_crm.deal_url(str(custom["crm_deal_id"]))}

    name, email = _conv_sender(conv)
    subject, body_text = _conv_first_incoming_body(messages)
    category_display = (custom.get("email_category_v2") or {}).get("display_name") \
                       or (custom.get("email_category_v2") or {}).get("category") \
                       or "Chatwoot Deal"

    # Deal-details gate: for bulk order / FHC / door the client requires the
    # customer's phone + city (gathered by the auto-ask flow) before any deal is
    # created. Block until both are captured — the bridge keeps requesting them.
    _gate_cat = (custom.get("email_category_v2") or {}).get("category") \
                or custom.get("phase2_category") or ""
    _captured = custom.get("deal_customer_details") or {}
    if (config.DEAL_DETAILS_GATE_ENABLED and _gate_cat in _DEAL_DETAILS_CATEGORIES
            and not (_captured.get("phone") and _captured.get("city"))):
        # The gate may never have captured phone + city on this conversation —
        # e.g. a bulk order with an uncertain region posts the "route manually"
        # card and returns BEFORE the deal-details gate runs. Rather than block a
        # deal the customer already qualified, extract phone + city from the
        # thread now; only 422 if they genuinely aren't there.
        recover_text = "\n".join(
            [subject, body_text] + [(m.get("content") or "") for m in messages
                                    if m.get("message_type") in (0, "incoming")])
        r_phones = _extract_phones(recover_text)
        r_city = ""
        if r_phones:
            try:
                r_city = (await _deal_details_gate_llm(name, recover_text, True)).get("city") or ""
            except Exception as e:
                print(f"[crm] deal-details recover city failed for conv {conv_id}: {e}")
        if r_phones and r_city:
            _captured = {"phone": r_phones[0], "city": r_city,
                         "captured_at": _now_iso(), "recovered": True}
            try:
                await chatwoot.merge_custom_attributes(
                    int(conv_id), {"deal_customer_details": _captured})
            except Exception as e:
                print(f"[crm] deal-details recover merge failed for conv {conv_id}: {e}")
            print(f"[crm] conv {conv_id}: recovered phone + city ({r_city}) at Create Deal")
        else:
            raise HTTPException(422, {
                "code": "deal_details_missing",
                "message": "Customer phone + city are required before creating this "
                           "deal. The bridge is auto-requesting them — the deal can "
                           "be created once the customer replies with both."})
    # Feed the confirmed city into owner routing so it beats an AI guess.
    if _captured.get("city"):
        body_text = f"City: {_captured['city']}. {body_text}"

    # Deal-qualification flow: Govt buyer → govt owner; otherwise
    # location-wise owner. Ambiguous buyer type → 409 so the panel asks the
    # agent to pick Government/Private. Unresolvable location → 422, do NOT
    # tag CRM (client rule).
    sector_override = (body.get("sector") or "").lower()
    owner = await _resolve_deal_owner(custom, body_text, subject, email,
                                      sector_override=sector_override)
    if owner.get("sector_unclear"):
        raise HTTPException(409, owner.get("reason") or "buyer type unclear")
    if owner.get("configured") and owner.get("location") is None:
        raise HTTPException(
            422, owner.get("reason")
            or "location could not be determined — not tagging to CRM")
    owner_id = owner.get("owner_id", "")

    # Remember the agent's sector decision so re-runs / other flows see it.
    if sector_override in ("government", "private"):
        try:
            cat_v2 = custom.get("email_category_v2") or {}
            await chatwoot.merge_custom_attributes(int(conv_id), {
                "email_category_v2": {
                    **cat_v2,
                    "sector": sector_override,
                    "sector_reason": "Chosen by agent at deal creation.",
                }})
        except Exception as e:
            print(f"[crm] merge agent sector failed for conv {conv_id}: {e}")

    if config.ZOHO_CRM_DRY_RUN:
        return {"deal_id": "", "created": False, "dry_run": True,
                "message": f"[dry-run] would create Deal for {email} "
                           f"(owner {owner.get('owner_email') or 'default'})"}

    # Deal needs a linked Contact. If none yet, create/find one first (owned by
    # the same location owner).
    try:
        contact_id = await _ensure_crm_contact(int(conv_id), conv, owner_id=owner_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"CRM Contact resolve failed: {e}")

    # Record layout — the flow's product sub-type split: full home
    # customization deals go on the "Home Studio" layout (designers),
    # everything else on "Standard" (sales). Home Studio is applied only when
    # ZOHO_CRM_HOME_STUDIO_LAYOUT is set — the client's Home Studio layout has
    # no pipeline configured yet, and a deal created on it would fail stage
    # validation.
    deal_category = (custom.get("email_category_v2") or {}).get("category") \
                    or custom.get("phase2_category") or ""
    layout_name = "Standard"
    deal_stage  = ""  # empty → create_deal uses ZOHO_CRM_DEAL_DEFAULT_STAGE
    if (deal_category in config.ZOHO_CRM_HOME_STUDIO_CATEGORIES
            and config.ZOHO_CRM_HOME_STUDIO_LAYOUT):
        layout_name = config.ZOHO_CRM_HOME_STUDIO_LAYOUT
        deal_stage  = config.ZOHO_CRM_HOME_STUDIO_STAGE

    # The client's Standard Deals layout has several MANDATORY fields the
    # bridge must fill (creation fails with MANDATORY_NOT_FOUND otherwise).
    # is_project drives the flow's "Scale?" split: bulk orders + government
    # buyers are Projects, everything else is Retail.
    is_project = (owner.get("location") == "govt"
                  or deal_category == "project_bulk_order")
    extra_fields = dict(config.ZOHO_CRM_DEAL_EXTRA_FIELDS)  # static env defaults
    if config.ZOHO_CRM_BUSINESS_TYPE_FIELD:
        # "Business Type" picklist (Retail / Project / Retail A & ID) —
        # "Retail A & ID" is agent-set in CRM afterwards.
        extra_fields[config.ZOHO_CRM_BUSINESS_TYPE_FIELD] = \
            "Project" if is_project else "Retail"
    # Pipeline — retail deals go on the Retail pipeline, project/govt deals
    # on the tender pipeline. Empty config = field not sent (Zoho uses the
    # layout's default pipeline; the entry stage exists in both).
    pipeline = (config.ZOHO_CRM_PIPELINE_PROJECT if is_project
                else config.ZOHO_CRM_PIPELINE_RETAIL)
    if pipeline:
        extra_fields.setdefault("Pipeline", pipeline)
    # Amount + Closing_Date are required but unknowable from an enquiry —
    # defaults the agent refines during qualification.
    extra_fields.setdefault("Amount", config.ZOHO_CRM_DEAL_AMOUNT_DEFAULT)
    closing = (datetime.now(timezone.utc)
               + timedelta(days=config.ZOHO_CRM_DEAL_CLOSING_DAYS))
    extra_fields.setdefault("Closing_Date", closing.strftime("%Y-%m-%d"))
    # Structured fields → the Deal record (all env-gated by api_name so a
    # wrong/unset name can't fail the create):
    #   Mobile ← gate-captured phone, else the Chatwoot contact's number
    #   City   ← gate-captured city
    #   Email  ← the inbox sender's email address
    deal_phone = (_captured.get("phone")
                  or ((conv.get("meta") or {}).get("sender") or {}).get("phone_number") or "")
    if config.ZOHO_CRM_MOBILE_FIELD and deal_phone:
        extra_fields.setdefault(config.ZOHO_CRM_MOBILE_FIELD, str(deal_phone))
    if config.ZOHO_CRM_CITY_FIELD and _captured.get("city"):
        extra_fields.setdefault(config.ZOHO_CRM_CITY_FIELD, str(_captured["city"]))
    if config.ZOHO_CRM_EMAIL_FIELD and email:
        extra_fields.setdefault(config.ZOHO_CRM_EMAIL_FIELD, str(email))

    description = await _deal_description(
        conv_id=int(conv_id), conv=conv, messages=messages,
        name=name, email=email, subject=subject,
        category_display=category_display, owner=owner,
        layout_name=layout_name)
    deal_name = f"{name or email} — {category_display}"[:255]
    try:
        deal = await zoho_crm.create_deal(
            contact_id=contact_id, deal_name=deal_name,
            description=description, source="Chatwoot", owner_id=owner_id,
            vertical=owner.get("vertical", ""), layout_name=layout_name,
            stage=deal_stage, extra_fields=extra_fields,
        )
    except Exception as e:
        raise HTTPException(500, f"CRM create_deal failed: {e}")

    deal_id = str(deal.get("id") or "")
    try:
        await chatwoot.merge_custom_attributes(
            int(conv_id), {"crm_deal_id": deal_id,
                           "crm_deal_url": zoho_crm.deal_url(deal_id)})
    except Exception as e:
        print(f"[crm] merge crm_deal_id failed for conv {conv_id}: {e}")

    # Tag the conversation so agents can see/filter every deal-creating enquiry,
    # plus a per-vertical label for what kind of deal it was. Permanent markers
    # (kept even after the deal auto-resolves the conversation).
    try:
        cat_key = (custom.get("email_category_v2") or {}).get("category") or ""
        deal_labels = [DEAL_CREATED_LABEL]
        vlabel = _DEAL_VERTICAL_LABEL.get(cat_key)
        if vlabel:
            deal_labels.append(vlabel)
        for lbl in deal_labels:
            await _label_conversation(int(conv_id), lbl)
    except Exception as e:
        print(f"[crm] deal-created label failed for conv {conv_id}: {e}")

    # Deal is created → the "Create Deal" agent-need is satisfied; drop it from the
    # unified section (the permanent deal-created / deal-<vertical> tags stay).
    await _clear_agent_needed(int(conv_id), conv)

    try:
        agent_name = body.get("agent_name") or "an agent"
        await chatwoot.post_private_note(
            int(conv_id),
            f"✅ CRM Deal created by {agent_name} — "
            f"[View Deal in Zoho CRM]({zoho_crm.deal_url(deal_id)})",
        )
    except Exception as e:
        print(f"[crm] Deal audit note failed for conv {conv_id}: {e}")

    # A created deal means the enquiry is qualified and handled → resolve the
    # conversation so it leaves the open queue (bulk orders in particular no
    # longer forward, so deal creation is their close signal). Customer replies
    # auto-reopen in Chatwoot. Best-effort — never fail the deal response.
    if config.RESOLVE_AFTER_DEAL:
        try:
            await chatwoot.toggle_status(int(conv_id), "resolved")
        except Exception as e:
            print(f"[crm] resolve-after-deal failed for conv {conv_id}: {e}")

    return {"deal_id": deal_id, "created": True,
            "url": zoho_crm.deal_url(deal_id)}


# ── Spam-review digest endpoint ───────────────────────────────────────────
# Hit this daily (manually, via Task Scheduler / cron, or a CI cron). Pulls
# every conversation currently SNOOZED + labelled "spam" and either returns
# the list as JSON or posts a human-readable digest into the conversation
# identified by SPAM_DIGEST_INBOX_ID (if configured).
#
# AUTH: requires X-Bridge-Token to match config.BRIDGE_OPS_TOKEN.
# The Chatwoot webhook route is HMAC-verified (per-payload signature) but
# whoever schedules this endpoint doesn't have a webhook payload to sign,
# so a shared-secret header is the right primitive. Fail-CLOSED when no
# token is configured — pre-review this endpoint was silently unauthed
# and `?post=true` could be used to inject a private note into any
# conversation by an unauthenticated caller.
@app.get("/spam-digest")
async def spam_digest(
    post: bool = False,
    limit: int = Query(50, ge=1, le=200),
    x_bridge_token: Optional[str] = Header(None),
):
    if not config.BRIDGE_OPS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="BRIDGE_OPS_TOKEN not configured — /spam-digest disabled",
        )
    if not x_bridge_token or not hmac.compare_digest(
        x_bridge_token, config.BRIDGE_OPS_TOKEN
    ):
        raise HTTPException(status_code=401, detail="bad or missing X-Bridge-Token")

    try:
        candidates = await chatwoot.search_snoozed_spam_since()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    rows = []
    for c in candidates[:limit]:
        meta = (c.get("meta") or {}).get("sender") or {}
        attrs = c.get("custom_attributes") or {}
        classifier_meta = attrs.get("email_classifier") or {}
        rows.append({
            "id":            c.get("id"),
            "contact_name":  meta.get("name"),
            "contact_email": meta.get("email"),
            "status":        c.get("status"),
            "updated_at":    c.get("updated_at"),
            "confidence":    classifier_meta.get("confidence"),
            "bypass_reason": classifier_meta.get("bypass_reason"),
            "url": (
                f"{config.CHATWOOT_BASE_URL}/app/accounts/"
                f"{config.CHATWOOT_ACCOUNT_ID}/conversations/{c.get('id')}"
            ),
        })

    posted = False
    if post and rows and config.SPAM_DIGEST_INBOX_ID:
        body_lines = [
            f"📋 **Spam-review digest** — {_now_iso()}",
            f"{len(rows)} conversation(s) auto-snoozed as spam.",
            "",
        ]
        for i, r in enumerate(rows, 1):
            line = (
                f"{i}. [#{r['id']}]({r['url']}) — "
                f"{r['contact_name'] or 'Unknown'} "
                f"<{r['contact_email'] or 'no-email'}>"
            )
            if r["confidence"]:
                line += f" — confidence {r['confidence']}/10"
            body_lines.append(line)
        body_lines.append("")
        body_lines.append(
            "_Click any conversation link to review. If it's actually a real "
            "customer, reopen the conversation and remove the 'spam' label — "
            "the bridge will respect your override via the idempotency check._"
        )
        try:
            await chatwoot.post_private_note(
                config.SPAM_DIGEST_INBOX_ID, "\n".join(body_lines)
            )
            posted = True
        except Exception as e:
            print(f"[spam-digest] post failed: {e}")

    return {"ok": True, "count": len(rows), "posted": posted, "rows": rows}
