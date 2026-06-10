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
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from langfuse import get_client

import config
import chatwoot
import classifier
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
        ticket = await zoho.create_ticket(data)
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
        ticket = await zoho.create_ticket(data, priority=priority, due_at=due_at)
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
    raw = source or ""
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
    else:
        label_source = f" ({raw})" if raw else ""

    # 1. Hunt for related tickets (best-effort)
    related = await zoho.search_tickets(subject, exclude_id=ticket_id, limit=3)
    if related:
        print(f"[zoho] found {len(related)} related tickets for conv {conv_id}: "
              + ", ".join(f"#{r.get('number') or r.get('id')}" for r in related))
    else:
        print(f"[zoho] no related tickets found for conv {conv_id} "
              f"(subject={subject[:60]!r})")

    # 2. Compose the private note
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

    # Confidence-aware actions
    if email_category == "spam":
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

    if email_category in ("promotional", "automated"):
        print(f"[spam] conv {conv_id} labelled '{email_category}', keeping in open queue")
        return {"classified_email_type": email_category, "auto_handled": True}

    # email_category == "legitimate" → second LLM call to pick a team.
    # NOTE: this is the team-routing classifier and is INTENTIONALLY a
    # separate call from classify_email_type for now — it has a different
    # response shape (single word) and was already in production before
    # the email-type classifier was added. Worth folding into one prompt
    # later if cost matters; today it's ~$0.0001/msg with gpt-4o-mini.
    print(f"[classify] classifying conv={conv_id} inbox={inbox_name!r} content={content[:60]!r}")
    team_key = await classifier.classify(content, inbox_name)
    team_id  = config.TEAM_IDS.get(team_key)
    print(f"[classify] → team={team_key} id={team_id}")

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
    if should_escalate:
        try:
            ticket = await zoho.create_ticket(data)
            zoho_ticket = ticket.get("id")
            print(f"[zoho] ticket created for conv {conv_id}: {zoho_ticket}")
            await _surface_ticket_in_chatwoot(
                conv_id, ticket, source=f"auto_{escalation_label}"
            )
        except Exception as e:
            print(f"[zoho] ERROR creating ticket for conv {conv_id} "
                  f"(team={team_key}, reason={escalation_label}): {e}")

    return {
        "classified":         team_key,
        "assigned_team_id":   team_id,
        "zoho_ticket_id":     zoho_ticket,
        "escalation_reason":  escalation_label if should_escalate else None,
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
