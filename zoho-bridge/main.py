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
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from langfuse import get_client

import config
import chatwoot
import classifier
import document_extractor
import summarizer
import zoho
import google_reviews as gr
import reviews_poller
import reviews_state

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
    summary = await summarizer.summarize_conversation(messages) if messages else {}
    return messages, summary


async def handle_status_changed(data: dict) -> dict:
    conv       = data.get("conversation") or data
    conv_id    = conv.get("id") or data.get("id")
    new_status = (conv.get("status") or data.get("status") or "").lower()
    if new_status != "open":
        return {"ignored": True, "reason": f"status={new_status}"}
    # Comments are handled by the DM bot — never raise a Zoho ticket for them.
    if _is_comment_conversation(conv):
        print(f"[handoff] conv {conv_id} is a comment — handled by DM bot, no Zoho ticket")
        return {"ignored": True, "reason": "comment_conversation"}
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
        note = "🔗 **Conversation attached to Zoho Desk ticket**"
    else:
        note = "🎫 **Zoho Desk ticket created**"
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
async def _pause_for_agent_decision(conv_id: int,
                                    sender_email: str,
                                    escalation_label: str,
                                    candidates: list[dict]) -> None:
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
        "suggested_at":     _now_iso(),
    }
    try:
        await chatwoot.merge_custom_attributes(conv_id, {"pending_zoho_ticket": pending})
    except Exception as e:
        print(f"[zoho-dedup] merge_custom_attributes failed for {conv_id}: {e}")

    # Post a private note so agents notice without watching the sidebar.
    lines = [
        "🎫 **Zoho ticket creation paused — agent decision needed**",
        "",
        f"This contact ({sender_email}) already has open Zoho tickets that "
        f"may be related. The bridge would have auto-escalated this conversation "
        f"as **{escalation_label}**, but to avoid duplicate tickets it's "
        f"waiting for you to decide.",
        "",
        "Existing open tickets:",
    ]
    for t in candidates[:5]:
        num = f"#{t.get('number')}" if t.get("number") else (t.get("id") or "?")
        subj = (t.get("subject") or "")[:80]
        url = t.get("url")
        if url:
            lines.append(f"- [{num}]({url}) — {subj}")
        else:
            lines.append(f"- {num} — {subj}")
    lines.append("")
    lines.append("→ Open the **Ticket decision** panel in the sidebar to "
                 "attach this conversation to one of the above, or create "
                 "a new ticket.")
    try:
        await chatwoot.post_private_note(conv_id, "\n".join(lines))
    except Exception as e:
        print(f"[zoho-dedup] post_private_note failed for {conv_id}: {e}")


async def _resolve_ticket_decision(conv_id: int, choice: str,
                                   target_ticket_id: Optional[str] = None) -> dict:
    """Execute the agent's choice on a paused ticket. Idempotent on success
    (clears pending_zoho_ticket regardless of which branch ran). Returns a
    small JSON-friendly dict for the HTTP endpoint to relay back."""
    if choice not in ("use_existing", "create_new"):
        raise ValueError(f"invalid choice: {choice!r}")

    # Pull the paused-decision context off the conversation.
    conv_data = await chatwoot.get_conversation(conv_id)
    attrs = conv_data.get("custom_attributes") or {}
    pending = attrs.get("pending_zoho_ticket") or {}
    if not pending:
        return {"resolved": False, "reason": "no_pending_decision"}

    escalation_label = pending.get("escalation_label") or "manual_handoff"
    candidates       = pending.get("candidates") or []

    # We reconstruct a synthetic webhook payload from the live conversation
    # so the existing create_ticket + _surface_ticket_in_chatwoot helpers
    # work unchanged. Cheaper than refactoring those to accept a Conversation
    # record directly.
    synthetic_payload = {"conversation": conv_data}

    result: dict = {"resolved": True, "choice": choice}

    if choice == "create_new":
        try:
            messages, summary = await _ticket_context(conv_id)
            ticket = await zoho.create_ticket(
                synthetic_payload, messages=messages, summary=summary
            )
            print(f"[zoho-dedup] conv {conv_id}: agent chose CREATE_NEW → "
                  f"ticket {ticket.get('id')}")
            await _surface_ticket_in_chatwoot(
                conv_id, ticket, source=f"auto_{escalation_label}"
            )
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

    return result


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

    if open_tickets:
        print(f"[zoho-dedup] conv {conv_id}: {len(open_tickets)} open ticket(s) "
              f"for {sender_email!r} — pausing auto-create")
        await _pause_for_agent_decision(
            conv_id=conv_id, sender_email=sender_email,
            escalation_label=escalation_label, candidates=open_tickets,
        )
        pending_decision = {"candidates": [t["id"] for t in open_tickets],
                            "source": escalation_label}
    else:
        try:
            messages, summary = await _ticket_context(conv_id)
            ticket = await zoho.create_ticket(data, messages=messages, summary=summary)
            zoho_ticket = ticket.get("id")
            print(f"[zoho] ticket created for conv {conv_id}: {zoho_ticket}")
            await _surface_ticket_in_chatwoot(conv_id, ticket, source=f"auto_{escalation_label}")
        except Exception as e:
            print(f"[zoho] ERROR creating ticket for conv {conv_id} "
                  f"(reason={escalation_label}): {e}")
    return zoho_ticket, pending_decision


async def _phase2_execute_actions(conv_id: int,
                                  category_result: dict,
                                  rule: Optional[dict],
                                  sender_name: str,
                                  sender_email: str,
                                  original_content: str,
                                  original_subject: str) -> list[str]:
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
    if not _EMAIL_CUSTOMER_ACK_ENABLED:
        audit.append("ℹ️ Customer acknowledgment is disabled (flag off).")
    elif template and sender_email:
        ack_body = (template.get("body") or "").format(
            customer_name    = name,
            original_subject = original_subject or "",
        )
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
            fwd_lines = [
                f"Forwarding the message below from {sender_name or sender_email} "
                "for your review and necessary action.",
                "",
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
            try:
                await chatwoot.send_outgoing_message(
                    conv_id,
                    forward_body,
                    to_emails  = forward_to,
                    cc_emails  = ", ".join(cc_list)  if cc_list  else None,
                    bcc_emails = ", ".join(bcc_list) if bcc_list else None,
                )
                audit.append(f"📨 Forwarded to {forward_to}.")
                if cc_list:
                    audit.append(f"Cc: {', '.join(cc_list)}")

                # Tag the conversation so agents can find all auto-forwarded
                # emails in one place via a Chatwoot saved View filtered by
                # this label. Best-effort — must not undo the forward.
                try:
                    await chatwoot.add_label(conv_id, "auto-forwarded")
                    audit.append("🏷️ Tagged auto-forwarded.")
                except Exception as e:
                    print(f"[phase2b] add_label failed for conv {conv_id}: {e}")
            except Exception as e:
                print(f"[phase2b] forward send failed for conv {conv_id}: {e}")
                audit.append(f"⚠️ Forward could not be sent: {e}")
    elif action == "in_channel":
        # In-channel categories aren't forwarded — the conversation stays
        # open for an agent to handle. Say so plainly in the note.
        audit.append("This conversation stays here for the team to assist.")

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

        results = await document_extractor.extract_for_message(
            content, attachments, message_id, seen_keys
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

    conv    = data.get("conversation") or {}
    conv_id = conv.get("id")
    print(f"[msg] conv_id={conv_id}")

    # Comment conversations belong to the in-Chatwoot DM bot, which replies
    # with a comment-specific prompt. The bridge must not run the spam/team
    # pipeline or escalate them to Zoho — bail out before any of that.
    if _is_comment_conversation(conv):
        print(f"[msg] conv {conv_id} is a comment — leaving to DM bot, skipping pipeline")
        return {"ignored": True, "reason": "comment_conversation"}

    # Document extraction (bills / receipts / order screenshots). MUST be
    # scheduled BEFORE the early returns below: the spam/team pipeline only
    # runs on a conversation's FIRST message, but bills arrive at ANY point
    # in a conversation. Fire-and-forget so a slow vision call never delays
    # the webhook response; gating + idempotency live inside the helper.
    if config.DOC_EXTRACTION_ENABLED:
        _schedule_document_extraction(data)

    # Idempotency: already-classified conversations skip both classifiers.
    existing_attrs   = conv.get("custom_attributes") or {}
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

    if bypass_reason is None:
        result = await classifier.classify_email_type(
            content, sender_email=sender_email, subject=real_subject
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
            content, sender_email=sender_email, subject=real_subject
        )
        rule = category_result.pop("rule", None)
        print(f"[category-v2] conv {conv_id}: category={category_result['category']!r} "
              f"confidence={category_result['confidence']} "
              f"action={category_result['action']!r}")
    except Exception as e:
        print(f"[category-v2] classify ERROR ({type(e).__name__}): {e}")

    category_confident = bool(category_result) and \
        category_result.get("category") != "fallback"

    # ── Decision A: spam/promotional only stops the flow when the
    # categorizer is ALSO uncertain. A confident business category wins. ──
    if email_category == "spam" and not category_confident:
        if classifier_conf >= config.SPAM_CONFIDENCE_THRESHOLD:
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
        return {
            "classified_email_type": "spam",
            "confidence":            classifier_conf,
            "auto_snoozed":          classifier_conf >= config.SPAM_CONFIDENCE_THRESHOLD,
        }

    if email_category in ("promotional", "automated") and not category_confident:
        print(f"[spam] conv {conv_id} labelled '{email_category}', keeping in open queue")
        return {"classified_email_type": email_category, "auto_handled": True}

    if email_category in ("spam", "promotional", "automated") and category_confident:
        print(f"[category-v2] '{email_category}' label overridden — confident "
              f"category {category_result['category']!r} wins; proceeding with "
              f"acknowledge/forward")

    # ── Categorizer ACTION (acknowledge + forward) + agent note ──────────
    # Phase 2A (PHASE_2_DRY_RUN=true): render a preview note, send nothing.
    # Phase 2B (PHASE_2_DRY_RUN=false): actually acknowledge + forward.
    if category_result is not None:
      try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "email_category_v2": {
                **category_result,
                "classified_at": _now_iso(),
                # Capture WHICH category was picked but stop short of
                # serialising the full YAML rule (forward_to / cc / bcc)
                # — Phase 2 will re-resolve it on the fly when forwarding.
                "display_name": (rule or {}).get("display_name"),
            },
        })

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
        if _PHASE_2_DRY_RUN:
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
        team_key = await classifier.classify(content, inbox_name)
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


@app.post("/chatwoot/resolve-ticket-decision")
async def chatwoot_resolve_ticket_decision(request: Request):
    """Called by the Chatwoot Rails proxy when an agent clicks
    [Attach to #N] or [Create new] in the Pending Ticket Decision panel.

    Body: {
      "conversation_id": int,
      "choice":          "use_existing" | "create_new",
      "target_ticket_id": str  // required when choice == "use_existing"
    }
    """
    body = await request.json()
    conv_id          = body.get("conversation_id")
    choice           = body.get("choice")
    target_ticket_id = body.get("target_ticket_id")
    if not conv_id or choice not in ("use_existing", "create_new"):
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
