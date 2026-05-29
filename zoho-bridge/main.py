# Chatwoot webhook receiver. Two responsibilities, two handlers:
#
#   1. message_created (first incoming)         → classify + assign team
#   2. conversation_status_changed (→ "open")   → create Zoho Desk ticket
#
# Each handler is self-contained; add more by writing a function and wiring
# it in the dispatcher at the bottom.

import hashlib
import hmac
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request

import config
import chatwoot
import classifier
import zoho

app = FastAPI()


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
    new_status = (conv.get("status") or data.get("status") or "").lower()
    if new_status != "open":
        return {"ignored": True, "reason": f"status={new_status}"}
    try:
        ticket = await zoho.create_ticket(data)
        return {"created": True, "ticket_id": ticket.get("id"),
                "ticket_number": ticket.get("ticketNumber")}
    except Exception as e:
        print(f"[handoff] ERROR creating Zoho ticket: {e}")
        return {"created": False, "error": str(e)}


# ── Handler: first incoming message → classify + assign team ──────────────
async def handle_message_created(data: dict) -> dict:
    msg_type = data.get("message_type")
    print(f"[msg] message_type={msg_type!r}")

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
        except Exception as e:
            print(f"[zoho] ERROR creating legal ticket for conv {conv_id}: {e}")

    return {"classified": team_key, "assigned_team_id": team_id, "zoho_ticket_id": zoho_ticket}


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
