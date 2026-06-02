# Chatwoot webhook receiver. Two responsibilities, two handlers:
#
#   1. message_created (first incoming)         → classify + assign team
#   2. conversation_status_changed (→ "open")   → create Zoho Desk ticket
#
# Each handler is self-contained; add more by writing a function and wiring
# it in the dispatcher at the bottom.

import asyncio
import hashlib
import hmac
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request

import config
import chatwoot
import classifier
import zoho
import google_reviews as gr
import reviews_poller
import reviews_state

app = FastAPI()


@app.on_event("startup")
async def _start_reviews_poller():
    # Boot-safe: run_forever() no-ops if Google isn't configured yet.
    asyncio.create_task(reviews_poller.run_forever())


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


# ── Handler: bot handoff → Zoho ticket ────────────────────────────────────
async def handle_status_changed(data: dict) -> dict:
    conv       = data.get("conversation") or data
    conv_id    = conv.get("id") or data.get("id")
    new_status = (conv.get("status") or data.get("status") or "").lower()
    if new_status != "open":
        return {"ignored": True, "reason": f"status={new_status}"}
    try:
        ticket = await zoho.create_ticket(data)
        await _surface_ticket_in_chatwoot(conv_id, ticket, source="manual_handoff")
        return {"created": True, "ticket_id": ticket.get("id"),
                "ticket_number": ticket.get("ticketNumber")}
    except Exception as e:
        print(f"[handoff] ERROR creating Zoho ticket: {e}")
        return {"created": False, "error": str(e)}


# ── Helper: visible bubble + sidebar pane data after ticket creation ──────
async def _surface_ticket_in_chatwoot(
    conv_id: Optional[int], ticket: dict, source: str
) -> None:
    """After a Zoho ticket is created, do two things in Chatwoot:
      1. Post a private note ('🎫 Zoho Desk ticket #X created') so agents see a
         bubble inline in the conversation.
      2. Merge the ticket metadata into conversation.additional_attributes so
         the Chatwoot dashboard's sidebar can render a 'Zoho Ticket' panel.
    Both are best-effort and never raise (a ticket was created either way).
    """
    if not conv_id:
        return
    ticket_id     = ticket.get("id")
    ticket_number = ticket.get("ticketNumber") or ticket.get("ticket_number")
    web_url       = ticket.get("webUrl") or (
        f"{config.ZOHO_DESK_URL}/agent/tickets/details/{ticket_id}"
        if ticket_id else None
    )

    label_source = {
        "manual_handoff":  "manual handoff",
        "auto_legal":      "auto-routed (Legal)",
    }.get(source, source or "")
    label_source = f" ({label_source})" if label_source else ""

    note = "🎫 **Zoho Desk ticket created**"
    if ticket_number:
        note += f" — [#{ticket_number}]({web_url})" if web_url else f" — #{ticket_number}"
    elif ticket_id:
        note += f" — [{ticket_id}]({web_url})" if web_url else f" — {ticket_id}"
    note += label_source

    try:
        await chatwoot.post_private_note(conv_id, note)
    except Exception as e:
        print(f"[zoho] post_private_note failed for conv {conv_id}: {e}")

    try:
        await chatwoot.merge_additional_attributes(conv_id, {
            "zoho_ticket": {
                "id":         ticket_id,
                "number":     ticket_number,
                "url":        web_url,
                "source":     source,
            }
        })
    except Exception as e:
        print(f"[zoho] merge_additional_attributes failed for conv {conv_id}: {e}")


# ── Handler: first incoming message → classify + assign team ──────────────
async def handle_message_created(data: dict) -> dict:
    msg_type = data.get("message_type")
    print(f"[msg] message_type={msg_type!r}")

    # Outgoing on the reviews inbox = an agent's public reply → post to Google.
    if msg_type in (1, "outgoing"):
        return await handle_review_reply(data)

    # Only act on incoming messages (message_type: 0 in Chatwoot)
    if msg_type not in (0, "incoming"):
        print(f"[msg] ignoring — not incoming")
        return {"ignored": True, "reason": "not_incoming"}

    conv    = data.get("conversation") or {}
    conv_id = conv.get("id")
    print(f"[msg] conv_id={conv_id}")

    # Idempotency: if a team is already set, skip.
    team_meta = (conv.get("meta") or {}).get("team")
    if team_meta:
        print(f"[msg] ignoring — team already set: {team_meta}")
        return {"ignored": True, "reason": "team_already_set"}

    content    = data.get("content") or ""
    inbox_name = (data.get("inbox") or {}).get("name", "")
    if not conv_id:
        return {"ignored": True, "reason": "no_conversation_id"}

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

    # Legal emails → also create Zoho Desk ticket immediately
    zoho_ticket = None
    if team_key == "legal":
        try:
            ticket = await zoho.create_ticket(data)
            zoho_ticket = ticket.get("id")
            print(f"[zoho] legal ticket created: {zoho_ticket}")
            await _surface_ticket_in_chatwoot(conv_id, ticket, source="auto_legal")
        except Exception as e:
            print(f"[zoho] ERROR creating legal ticket for conv {conv_id}: {e}")

    return {"classified": team_key, "assigned_team_id": team_id, "zoho_ticket_id": zoho_ticket}


# ── Handler: agent reply on a review → post to Google ──────────────────────
async def handle_review_reply(data: dict) -> dict:
    # Only reviews inbox; skip private notes and our own auto-replies.
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

    # Prefer the stored mapping; fall back to the conversation's custom attribute.
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
