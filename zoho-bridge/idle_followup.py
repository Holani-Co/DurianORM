# WhatsApp idle follow-up + auto-resolve (client request, Oct 2026). Most
# WhatsApp FHC/Furniture chats stall after DurianAI's reply and sit open until
# someone resolves them by hand (75 open on FHC, ~70 waiting on the customer).
#
# For every open chat the AI owns on IDLE_FOLLOWUP_INBOXES whose last public
# message is ours:
#   1. ~IDLE_FOLLOWUP_AFTER_HOURS after the customer's last message — still
#      inside WhatsApp's 24h service window, and never at night — DurianAI sends
#      ONE short, chat-aware follow-up (a fixed line if the draft fails a
#      guardrail);
#   2. still silent IDLE_RESOLVE_AFTER_HOURS after that follow-up — or after our
#      last message when the window had already closed, which also clears the
#      old backlog — the chat is resolved with a private note.
# A customer reply reopens it (the inboxes are lock_to_single_conversation) and
# starts a fresh cycle. Chats assigned to a person, waiting on a team
# (agent-needed) or stood down are never touched.
import asyncio
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import chatwoot
import config
import social_agent as sa

IST = ZoneInfo("Asia/Kolkata")
_WINDOW = 23.5 * 3600            # WhatsApp's 24h service window, with margin
_EARLIEST = 12 * 3600            # never follow up sooner than this
_QUIET_START, _QUIET_END = 21, 9  # no follow-ups 9 PM – 9 AM IST
_FOR_ATTR, _AT_ATTR = "idle_followup_for", "idle_followup_at"

_FALLBACK = {
    "fhc": ("Hi{name}! Just checking in — would you like any help with your home "
            "interiors? Reply here anytime and we'll take it forward."),
    "furniture": ("Hi{name}! Just checking in — can we help you with anything from "
                  "Durian? Reply here anytime and we'll take it forward."),
}


def _quiet(ts: float) -> bool:
    h = datetime.fromtimestamp(ts, IST).hour
    return h >= _QUIET_START or h < _QUIET_END


def _daytime_in(start: float, end: float) -> bool:
    t = start
    while t < end:
        if not _quiet(t):
            return True
        t += 900
    return False


def follow_up_due(now: float, last_in: float) -> bool:
    """Send the follow-up now? Inside [AFTER, window) at a daytime moment; when
    that whole span falls at night, at the last daytime moment before it (but
    never sooner than _EARLIEST)."""
    age = now - last_in
    if age >= _WINDOW or age < _EARLIEST or _quiet(now):
        return False
    target = last_in + config.IDLE_FOLLOWUP_AFTER_HOURS * 3600
    if now >= target:
        return True
    end = last_in + _WINDOW
    # Night covers the rest of the target span → go now if this is the last
    # daytime sweep before the night starts.
    return not _daytime_in(target, end) and _quiet(now + config.IDLE_SWEEP_MINUTES * 60)


def decide(conv: dict, messages: list, now: float) -> str:
    """'' (leave) | 'follow_up' | 'resolve' for one open conversation."""
    ca = conv.get("custom_attributes") or {}
    assignee = (conv.get("meta") or {}).get("assignee") or {}
    if ca.get("agent_mode_standdown") or (assignee and not sa.is_bot_agent(assignee)) \
            or "agent-needed" in (conv.get("labels") or []):
        return ""
    public = [m for m in messages or [] if not m.get("private")
              and m.get("message_type") in (0, 1, 3, "incoming", "outgoing", "template")]
    incoming = [m for m in public if m.get("message_type") in (0, "incoming")]
    if not public or not incoming or public[-1] in incoming:
        return ""                    # never wrote to us, or waiting on us
    last_in, last_out = incoming[-1], public[-1]
    t_in, t_out = float(last_in.get("created_at") or 0), float(last_out.get("created_at") or 0)
    if str(ca.get(_FOR_ATTR) or "") == str(last_in.get("id")):
        at = float(ca.get(_AT_ATTR) or 0)
        return "resolve" if now - at >= config.IDLE_RESOLVE_AFTER_HOURS * 3600 else ""
    if follow_up_due(now, t_in):
        return "follow_up"
    if now - t_in >= _WINDOW and now - t_out >= config.IDLE_RESOLVE_AFTER_HOURS * 3600:
        return "resolve"             # window closed without a follow-up
    return ""


async def _draft(conv: dict, messages: list, vertical: str) -> str:
    """One short chat-aware nudge from the agent model, or the fixed line."""
    name = ((conv.get("meta") or {}).get("sender") or {}).get("name") or ""
    first = name.split()[0] if name and not re.search(r"\d", name) else ""
    fallback = _FALLBACK.get(vertical, _FALLBACK["furniture"]).format(
        name=f" {first}" if first else "")
    lines = [f"{'Customer' if m.get('message_type') in (0, 'incoming') else 'Durian'}: "
             f"{(m.get('content') or '').strip()[:400]}"
             for m in messages[-12:] if not m.get("private") and (m.get("content") or "").strip()]
    transcript = "\n".join(lines)
    try:
        r = await sa._client.chat.completions.create(
            model=config.SOCIAL_AGENT_MODEL, max_completion_tokens=400,
            messages=[
                {"role": "system", "content":
                 "You are Durian's WhatsApp assistant (furniture, doors and full home "
                 "interiors). The customer stopped replying about 20 hours ago. Write "
                 "ONE short, warm follow-up (at most 2 sentences, English, plural "
                 "'we') that picks up exactly where the chat left off and offers the "
                 "single most useful next step from it. Use the customer's first name "
                 "only if the chat shows it. NO prices, numbers, links, addresses, "
                 "offers or promises, nothing the chat doesn't already say, no "
                 "pressure, at most one emoji. Reply with the message text only."},
                {"role": "user", "content": f"CHAT:\n{transcript}"}])
        text = sa.scrub((r.choices[0].message.content or "").strip().strip('"'), "dm")
    except Exception as e:  # noqa: BLE001
        print(f"[idle] follow-up draft failed for conv {conv.get('id')}: {e}")
        return fallback
    if (not text or len(text) > 320 or sa.off_scope_line(text) or sa.link_violation(text)
            or sa.ungrounded_facts(text, transcript) or sa.unbacked_claim(text)):
        return fallback
    return text


async def _list_open(inbox_id: int, max_pages: int = 20) -> list[dict]:
    out = []
    async with httpx.AsyncClient(timeout=20) as client:
        for page in range(1, max_pages + 1):
            r = await client.get(chatwoot._acct_url("/conversations"), headers=chatwoot._headers(),
                                 params={"status": "open", "inbox_id": inbox_id,
                                         "assignee_type": "all", "page": page})
            if r.status_code >= 300:
                print(f"[idle] list inbox {inbox_id} failed [{r.status_code}]")
                break
            data = r.json().get("data") or {}
            rows = data.get("payload") if isinstance(data, dict) else []
            if not rows:
                break
            out += rows
    return out


async def sweep_once(now: float | None = None) -> dict:
    now = now or time.time()
    counts = {"followed_up": 0, "resolved": 0, "errors": 0}
    for inbox_id in config.IDLE_FOLLOWUP_INBOXES:
        vertical = (config.SOCIAL_AGENT_WHATSAPP_INBOXES or {}).get(str(inbox_id), "furniture")
        for conv in await _list_open(inbox_id):
            if now - float(conv.get("last_activity_at") or now) < _EARLIEST:
                continue             # active in the last 12h — nothing due
            cid = conv["id"]
            try:
                msgs = await chatwoot.get_conversation_messages_raw(cid)
                action = decide(conv, msgs, now)
                if action == "follow_up":
                    last_in = [m for m in msgs if m.get("message_type") in (0, "incoming")][-1]
                    text = await _draft(conv, msgs, vertical)
                    await chatwoot.create_message(cid, text, message_type="outgoing",
                                                  content_attributes={"source": "ai_auto_reply",
                                                                      "via": "idle_followup"})
                    await chatwoot.merge_custom_attributes(cid, {
                        _FOR_ATTR: str(last_in.get("id")), _AT_ATTR: int(now)})
                    await chatwoot.post_private_note(
                        cid, f"🤖 Follow-up sent — no reply for "
                             f"{int((now - float(last_in.get('created_at') or now)) // 3600)}h. "
                             f"Auto-resolves in {config.IDLE_RESOLVE_AFTER_HOURS}h if still "
                             "no reply.")
                    counts["followed_up"] += 1
                elif action == "resolve":
                    await chatwoot.post_private_note(
                        cid, "🤖 Auto-resolved — the customer didn't reply. It reopens "
                             "automatically if they write again.")
                    await chatwoot.toggle_status(cid, "resolved")
                    counts["resolved"] += 1
            except Exception as e:  # noqa: BLE001
                counts["errors"] += 1
                print(f"[idle] conv {cid} failed: {e}")
            await asyncio.sleep(0.2)
    if counts["followed_up"] or counts["resolved"] or counts["errors"]:
        print(f"[idle] sweep: {counts}")
    return counts


async def run_forever():
    """Sweep every IDLE_SWEEP_MINUTES. Boot-safe; off unless enabled."""
    if not (config.IDLE_FOLLOWUP_ENABLED and config.IDLE_FOLLOWUP_INBOXES):
        print("[idle] disabled (IDLE_FOLLOWUP_ENABLED not true) — not started")
        return
    print(f"[idle] started · inboxes {config.IDLE_FOLLOWUP_INBOXES} · follow-up "
          f"{config.IDLE_FOLLOWUP_AFTER_HOURS}h · resolve +{config.IDLE_RESOLVE_AFTER_HOURS}h")
    while True:
        try:
            await sweep_once()
        except Exception as e:  # noqa: BLE001
            print(f"[idle] sweep error: {e}")
        await asyncio.sleep(config.IDLE_SWEEP_MINUTES * 60)
