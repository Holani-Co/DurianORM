# Store-locator link follow-up (client request, Nov 2026). A customer asks
# where a store is ("Hanumangarh mein available hai kya", "nearest showroom?"),
# DurianAI asks something back (pincode / city / details) and the customer goes
# quiet. ~STORE_LINK_FOLLOWUP_MINUTES later DurianAI sends ONE fixed message
# with the vertical's all-stores page so the customer can find an address
# themselves.
#
#   arm   — social_agent, right after it auto-sends a reply on an IG/FB DM or
#           WhatsApp chat: should_arm() decides from the customer's words, this
#           turn's skill calls and our reply. The timer is a row in a small
#           SQLite file, so a bridge restart doesn't lose it.
#   fire  — run_forever() checks due rows every minute and re-verifies on the
#           live conversation: the customer still hasn't written since, our
#           message is still the last one, no address went out meanwhile, the
#           chat is open, the AI still owns it, and no link was sent before.
#
# Never on public comments or the website chat, never on a vertical without a
# link (Doors today), at most once per conversation. Dark behind
# STORE_LINK_FOLLOWUP_ENABLED.
import asyncio
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager

import chatwoot
import config

CHANNELS = ("instagram", "facebook", "whatsapp")
_SENT_ATTR = "store_link_sent"

_DB_PATH = os.environ.get(
    "STORE_LINK_FOLLOWUP_DB",
    os.path.join(os.path.dirname(__file__), "store_link_followup.db"))
_lock = threading.Lock()

# The customer is asking where a store is. English, Hinglish and Devanagari.
_STORE_ASK_RE = re.compile(
    r"\b(stores?|showrooms?|show\s?rooms?|outlets?|branch(es)?|address|located|"
    r"location|nearest|near\s?(me|by)|nearby|dukaa?n|kaha+n|kidhar|kis\s+jagah)\b"
    r"|स्टोर|शोरूम|शो\s?रूम|दुकान|पता|कहाँ|कहां|किधर|एड्रेस|लोकेशन", re.I)
# "Is it available in <city>?" — a store question only when our reply treats it
# as one (asks for a pincode / city or talks about a store).
_AVAILABLE_IN_RE = re.compile(
    r"\bavailable\s+(in|at)\b|\b(me|mein|main|mai)\s+(available|milega|milegi|hai\s+kya)\b"
    r"|में\s+(अवेलेबल|उपलब्ध|मिलेगा|मिलेगी|है\s+क्या)", re.I)
_REPLY_STORE_RE = re.compile(
    r"\b(pin\s?code|city|area|locality|location|stores?|showrooms?|outlets?)\b", re.I)
# An order / service problem that mentions an address ("change my delivery
# address", "order kahan hai") is not a store question.
_NOT_STORE_RE = re.compile(
    r"\b(order\w*|deliver\w*|dispatch\w*|track\w*|refund\w*|return\w*|replace\w*|"
    r"damag\w*|broken|defect\w*|warranty|invoice|payment|paid|install\w*|"
    r"complain\w*|courier|shipment)\b"
    r"|ऑर्डर|डिलीवरी|रिफंड", re.I)
# Our reply asks the customer something.
_ASKS_RE = re.compile(
    r"\?|\b(share|send|tell|provide|let\s+us\s+know|drop|confirm|mention|"
    r"could\s+you|can\s+you|may\s+we|please)\b", re.I)
# An address already went out (store card / map link).
_ADDRESS_RE = re.compile(
    r"📍|maps\.app\.goo\.gl|goo\.gl/maps|google\.[a-z.]+/maps|maps\.google\.", re.I)

_MESSAGE = ("In case it helps, you can see all our stores and their addresses "
            "here: {link}\n\nOr share your pincode anytime and we'll point you to "
            "the nearest one.")


def links() -> dict:
    """{vertical: link} from STORE_LINK_FOLLOWUP_LINKS ("furniture:https://…,
    fhc:https://…"). A vertical not listed gets no follow-up."""
    out = {}
    for part in (config.STORE_LINK_FOLLOWUP_LINKS or "").split(","):
        vert, sep, url = part.strip().partition(":")
        if sep and vert.strip() and url.strip().startswith("http"):
            out[vert.strip().lower()] = url.strip()
    return out


def message_for(vertical: str) -> str:
    link = links().get((vertical or "").lower())
    return _MESSAGE.format(link=link) if link else ""


def should_arm(customer_text: str, meaning: str, reply: str,
               tool_results: list, earlier_outgoing: list[str]) -> bool:
    """Store question + we asked something back + no address given (this turn
    or before) → start the timer."""
    said = " ".join(t for t in (customer_text, meaning) if t)
    if not said.strip() or _NOT_STORE_RE.search(said):
        return False
    called = [tr for tr in tool_results or [] if tr.get("name") == "find_showrooms"]
    if any(isinstance(tr.get("result"), dict) and tr["result"].get("address_message")
           for tr in called):
        return False                     # this turn found the store
    if _ADDRESS_RE.search(reply or "") or any(_ADDRESS_RE.search(t or "")
                                              for t in earlier_outgoing or []):
        return False                     # an address already went out
    store_ask = bool(_STORE_ASK_RE.search(said) or called
                     or (_AVAILABLE_IN_RE.search(said) and _REPLY_STORE_RE.search(reply or "")))
    return store_ask and bool(_ASKS_RE.search(reply or ""))


# ── timer store ────────────────────────────────────────────────────────────
@contextmanager
def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init():
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending (
                conv_id    INTEGER PRIMARY KEY,
                for_msg_id TEXT NOT NULL,
                due        REAL NOT NULL,
                vertical   TEXT NOT NULL
            )
        """)


def arm(conv_id: int, for_msg_id, vertical: str, now: float | None = None) -> float:
    """Start (or restart) the timer for this conversation; returns the due time."""
    due = (now or time.time()) + config.STORE_LINK_FOLLOWUP_MINUTES * 60
    init()
    with _lock, _conn() as c:
        c.execute("INSERT INTO pending (conv_id, for_msg_id, due, vertical) "
                  "VALUES (?, ?, ?, ?) ON CONFLICT(conv_id) DO UPDATE SET "
                  "for_msg_id = excluded.for_msg_id, due = excluded.due, "
                  "vertical = excluded.vertical",
                  (int(conv_id), str(for_msg_id), due, vertical))
    return due


def _due(now: float) -> list[dict]:
    init()
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM pending WHERE due <= ? ORDER BY due", (now,))]


def _drop(conv_id: int) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM pending WHERE conv_id = ?", (int(conv_id),))


# ── firing ─────────────────────────────────────────────────────────────────
def still_due(conv: dict, messages: list, for_msg_id: str) -> str:
    """'' when the link should go out now, else why not."""
    import social_agent as sa  # lazy — social_agent imports this module
    ca = conv.get("custom_attributes") or {}
    if ca.get(_SENT_ATTR):
        return "link already sent"
    if (conv.get("status") or "open") != "open":
        return f"conversation {conv.get('status')}"
    assignee = (conv.get("meta") or {}).get("assignee") or {}
    if ca.get("agent_mode_standdown") or (assignee and not sa.is_bot_agent(assignee)):
        return "a person owns the chat"
    if any(str(lbl).startswith("agent-needed") for lbl in conv.get("labels") or []):
        return "waiting on a team"
    public = [m for m in messages or [] if not m.get("private")
              and m.get("message_type") in (0, 1, 3, "incoming", "outgoing", "template")]
    incoming = [m for m in public if m.get("message_type") in (0, "incoming")]
    if not public or not incoming or public[-1] in incoming:
        return "customer wrote last"
    if str(incoming[-1].get("id")) != str(for_msg_id):
        return "customer replied since"
    after = [m for m in public if float(m.get("created_at") or 0)
             >= float(incoming[-1].get("created_at") or 0)]
    if any(_ADDRESS_RE.search(m.get("content") or "") for m in after):
        return "an address went out since"
    return ""


async def fire(row: dict) -> str:
    """Send the link for one due row (or skip it). Returns what happened."""
    cid = int(row["conv_id"])
    text = message_for(row["vertical"])
    if not text:
        return "no link for this vertical"
    conv = await chatwoot.get_conversation(cid)
    msgs = await chatwoot.get_conversation_messages_raw(cid)
    why = still_due(conv, msgs, row["for_msg_id"])
    if why:
        return why
    await chatwoot.create_message(cid, text, message_type="outgoing",
                                  content_attributes={"source": "ai_auto_reply",
                                                      "via": "store_link_followup"})
    await chatwoot.merge_custom_attributes(cid, {_SENT_ATTR: True})
    try:
        await chatwoot.post_private_note(
            cid, f"🤖 Store-locator link sent — the customer asked about a store but "
                 f"didn't reply for {config.STORE_LINK_FOLLOWUP_MINUTES} min.")
    except Exception:
        pass
    return "sent"


async def sweep_once(now: float | None = None) -> dict:
    counts = {"sent": 0, "skipped": 0, "errors": 0}
    for row in _due(now or time.time()):
        try:
            outcome = await fire(row)
            counts["sent" if outcome == "sent" else "skipped"] += 1
            if outcome != "sent":
                print(f"[store-link] conv {row['conv_id']} skipped: {outcome}")
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            print(f"[store-link] conv {row['conv_id']} failed: {e}")
        _drop(row["conv_id"])            # one attempt per timer, sent or not
        await asyncio.sleep(0.2)
    if counts["sent"] or counts["errors"]:
        print(f"[store-link] sweep: {counts}")
    return counts


async def run_forever():
    """Check due timers every minute. Boot-safe; off unless enabled."""
    if not config.STORE_LINK_FOLLOWUP_ENABLED:
        print("[store-link] disabled (STORE_LINK_FOLLOWUP_ENABLED not true) — not started")
        return
    init()
    print(f"[store-link] started · after {config.STORE_LINK_FOLLOWUP_MINUTES} min · "
          f"links {links()}")
    while True:
        try:
            await sweep_once()
        except Exception as e:  # noqa: BLE001
            print(f"[store-link] sweep error: {e}")
        await asyncio.sleep(60)
