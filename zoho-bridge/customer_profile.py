# Customer profile — the durable, per-contact memory behind agent mode.
#
# Shape: an append-only EVENT LOG (exact timestamps + source message ids) plus
# a small consolidated section for aged-out history. Everything the model sees
# is FOLDED from events at read time, so a later "no" outranks an earlier
# "yes" purely by time. Stored on the Chatwoot CONTACT record
# (custom_attributes.durian_profile) so it follows the person across
# conversations and is visible/editable in the Chatwoot sidebar.
#
# ONE writer: the agent. Nothing enters the profile deterministically — no
# regex, no catalog scan, no state copying. At the end of every turn the
# agent submits finish.profile_updates: `set` (a current fact — phone,
# pincode, city, showroom) or `learn` (a dated event — interest, declined,
# preference, correction, budget, objection, promise, routed, deal_created,
# note), each with the customer's quote and the agent's reason. Code then
# runs the EVIDENCE GATE: the quote must appear in this turn's customer
# messages or in a tool result the agent actually received — the model can
# never dream a fact into someone's permanent record — and applies what
# passes idempotently (webhook re-fires cannot double-learn).
#
# Size: the rendered block is budgeted (~500 tokens); events compact into
# `consolidated` via a write-triggered pass (maybe_consolidate) — no cron,
# dormant contacts cost nothing.

import json
import re
from datetime import datetime, timedelta, timezone

import chatwoot

IST = timezone(timedelta(hours=5, minutes=30))
PROFILE_KEY = "durian_profile"

_PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}")
_PIN_RE = re.compile(r"\b[1-9]\d{5}\b")

# Injectable clock so tests can simulate messages spread across days.
_now_fn = lambda: datetime.now(IST)


def set_now(fn) -> None:
    global _now_fn
    _now_fn = fn


def now() -> datetime:
    return _now_fn()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _ts(epoch) -> str:
    try:
        return _iso(datetime.fromtimestamp(int(epoch), IST))
    except (TypeError, ValueError, OSError):
        return _iso(now())


def age_label(iso_or_epoch, ref: datetime | None = None) -> str:
    """'today 14:52' / 'yesterday' / '4 days ago (8 Aug)' — IST."""
    ref = ref or now()
    try:
        if isinstance(iso_or_epoch, (int, float)):
            dt = datetime.fromtimestamp(int(iso_or_epoch), IST)
        else:
            dt = datetime.fromisoformat(str(iso_or_epoch))
    except (TypeError, ValueError, OSError):
        return ""
    days = (ref.date() - dt.date()).days
    if days <= 0:
        return f"today {dt:%H:%M}"
    if days == 1:
        return "yesterday"
    return f"{days} days ago ({dt.day} {dt:%b})"


def msg_attrs(m) -> dict:
    """content_attributes as a dict, healing legacy double-encoded strings."""
    ca = (m or {}).get("content_attributes")
    if isinstance(ca, str):
        try:
            ca = json.loads(ca)
        except ValueError:
            ca = None
    return ca if isinstance(ca, dict) else {}


def empty_profile() -> dict:
    return {"v": 1, "updated_at": _iso(now()), "consolidated_at": None,
            "events_since_consolidation": 0, "identity": {}, "location": {},
            "commercial": {}, "linked_contacts": [], "events": [],
            "consolidated": {"stable_facts": [], "episodes": [],
                             "transitions": [], "stats": {}}}


# ── The contract: profile_updates → evidence gate → apply ─────────────────

LEARN_KINDS = ("interest", "declined", "preference", "correction", "budget",
               "objection", "promise", "routed", "deal_created", "note")
SET_FIELDS = ("identity.phone", "location.pincode", "location.city",
              "commercial.showroom")


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _tool_haystack(tool_results: list) -> str:
    """Everything the agent received from tools this turn, flattened — a
    learn/set about a routing or deal is verifiable against it."""
    parts = []
    for r in tool_results or []:
        try:
            parts.append(json.dumps(r.get("result") if isinstance(r, dict)
                                    else r, ensure_ascii=False))
        except (TypeError, ValueError):
            parts.append(str(r))
    return _norm(" ".join(parts))


def verify_updates(updates: list, incoming_texts: list[str],
                   tool_results: list, conv_id, inbox: str) -> list[dict]:
    """The evidence gate. Accept an update only when it is well-formed (a
    closed list of set fields / learn kinds) AND its quote appears in this
    turn's customer messages or tool results. Rejections are logged, never
    stored. Returns stamped, apply-ready updates."""
    msg_hay = _norm(" ".join(incoming_texts or []))
    tool_hay = _tool_haystack(tool_results)
    stamp = _iso(now())
    out = []
    for item in updates or []:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "").strip().lower()
        if op not in ("set", "learn"):
            # tolerate a missing op only when the item is unambiguous
            op = "set" if (item.get("field") and not item.get("kind")) else "learn"
        quote = _norm(item.get("quote"))[:160]
        note = str(item.get("note") or "").strip()[:200]
        if op == "set":
            field = str(item.get("field") or "").strip().lower()
            value = str(item.get("value") or "").strip()
            if field not in SET_FIELDS or not value:
                print(f"[profile] rejected set {field!r}: not a settable field / empty")
                continue
            what_for_log = f"{field}={value}"
        else:
            kind = str(item.get("kind") or "").strip().lower()
            value = str(item.get("what") or "").strip()
            if kind not in LEARN_KINDS or not value:
                print(f"[profile] rejected learn {kind!r}: not a learn kind / empty")
                continue
            what_for_log = f"{kind}/{value[:40]}"
        probe = quote[:80]
        if not probe or (probe not in msg_hay and probe not in tool_hay):
            print(f"[profile] rejected unverified update: {what_for_log} "
                  f"(quote {quote[:50]!r} not in this turn)")
            continue
        ev = {"op": op, "t": stamp, "conv": conv_id, "inbox": inbox,
              "quote": quote, "note": note}
        if op == "set":
            ev.update(field=field, value=value[:160])
        else:
            ev.update(kind=kind, what=value[:160])
        out.append(ev)
    return out


def apply_updates(profile: dict, updates: list[dict]) -> int:
    """Write verified updates. `set` replaces the current fact (value, when,
    why, quote); `learn` appends a dated event via merge_events (idempotent
    on time+kind+what, so a re-fired webhook cannot double-learn). Returns
    the number of changes made."""
    changed = 0
    for u in updates or []:
        if u.get("op") == "set":
            sec, _, fld = str(u.get("field") or "").partition(".")
            cur = (profile.get(sec) or {}).get(fld) or {}
            entry = {"value": u.get("value"), "t": u.get("t"),
                     "conv": u.get("conv"), "quote": u.get("quote") or "",
                     "note": u.get("note") or ""}
            profile.setdefault(sec, {})[fld] = entry
            if cur.get("value") != entry["value"]:
                changed += 1
                derived = None
                if cur.get("value"):
                    derived = {"kind": "correction", "field": fld,
                               "what": f"{fld}: {cur.get('value')} → {entry['value']}"}
                elif u.get("field") == "commercial.showroom":
                    derived = {"kind": "routed", "what": entry["value"]}
                if derived:
                    merge_events(profile, [{
                        "t": u.get("t"), "msg": None, "conv": u.get("conv"),
                        "inbox": u.get("inbox"), "quote": u.get("quote") or "",
                        "note": u.get("note") or "", **derived}])
            profile["updated_at"] = _iso(now())
        else:
            changed += merge_events(profile, [{
                "t": u.get("t"), "msg": None, "conv": u.get("conv"),
                "inbox": u.get("inbox"), "kind": u.get("kind"),
                "what": u.get("what"), "quote": u.get("quote") or "",
                "note": u.get("note") or ""}])
    return changed


# ── Merge + fold ────────────────────────────────────────────────────────────

def _key(ev: dict) -> tuple:
    return (ev.get("msg"), ev.get("kind"), str(ev.get("what"))[:60])


def merge_events(profile: dict, new_events: list[dict]) -> int:
    """Append events idempotently (webhook re-fires can't double-learn) and
    refresh the derived identity/location/commercial fields. Returns added."""
    seen = {_key(e) for e in profile.get("events") or []}
    added = 0
    for ev in sorted(new_events or [], key=lambda e: (e.get("t") or "", e.get("msg") or 0)):
        if _key(ev) in seen:
            continue
        profile.setdefault("events", []).append(ev)
        seen.add(_key(ev))
        added += 1
    if added:
        profile["events"].sort(key=lambda e: (e.get("t") or "", e.get("msg") or 0))
        profile["events_since_consolidation"] = \
            int(profile.get("events_since_consolidation") or 0) + added
        profile["updated_at"] = _iso(now())
    return added


def fold(profile: dict) -> dict:
    """Current-state view: active interests (newest first, minus later
    declines), the declined list, open promises. Time ordering does the work."""
    events = profile.get("events") or []
    declined, declined_full = [], []
    for ev in events:
        if ev.get("kind") == "declined":
            declined.append(str(ev.get("what") or "").lower())
            declined_full.append(ev)
    interests, seen = [], set()
    for ev in reversed(events):                      # newest first
        if ev.get("kind") not in ("interest", "shared_post"):
            continue
        what = str(ev.get("what") or "")
        low = what.lower()
        if low in seen:
            continue
        later_decline = any(
            d in low or low in d for d, de in
            [(str(x.get("what") or "").lower(), x) for x in declined_full]
            if de.get("t", "") > ev.get("t", ""))
        if later_decline:
            continue
        seen.add(low)
        interests.append(ev)
        if len(interests) >= 4:
            break
    promises = [e for e in events if e.get("kind") == "promise"][-2:]
    comments = [e for e in events if e.get("kind") == "commented"][-3:]
    return {"interests": interests, "declined": declined_full[-4:],
            "promises": promises, "comments": comments}


def render(profile: dict, contact_name: str, inbox: str = "") -> str:
    """The block inference reads — dated facts, phone pre-masked (the model
    never receives the full number), hard-budgeted."""
    ref = now()
    lines = [f"CUSTOMER PROFILE — {contact_name}"
             + (f" · via {inbox}" if inbox else "")]
    ident, loc, com = (profile.get("identity") or {}), \
        (profile.get("location") or {}), (profile.get("commercial") or {})
    ph = (ident.get("phone") or {}).get("value")
    if ph:
        digits = re.sub(r"\D", "", ph)[-4:]
        lines.append(f"- Contact number on file, ending {digits} "
                     f"(given {age_label((ident['phone'] or {}).get('t'), ref)})")
    for label, d in (("City", loc.get("city")), ("Pincode", loc.get("pincode"))):
        if d and d.get("value"):
            why = f" — {d['note']}" if d.get("note") else ""
            lines.append(f"- {label}: {d['value']} ({age_label(d.get('t'), ref)}){why}")
    f = fold(profile)
    if f["interests"]:
        parts = [f"{e.get('what')} ({age_label(e.get('t'), ref)})"
                 for e in f["interests"]]
        lines.append("- Interests, newest first: " + "; ".join(parts))
    if f["declined"]:
        parts = [f"{e.get('what')} (\"{e.get('quote', '')[:60]}\", "
                 f"{age_label(e.get('t'), ref)})" for e in f["declined"]]
        lines.append("- DECLINED — never raise again unless the customer does: "
                     + "; ".join(parts))
    if com.get("showroom", {}).get("value"):
        lines.append(f"- ALREADY routed to {com['showroom']['value']} showroom "
                     f"({age_label(com['showroom'].get('t'), ref)})")
    if com.get("deals"):
        lines.append(f"- CRM deal exists ({age_label(com['deals'][-1].get('t'), ref)})")
    if com.get("crm_contact_id", {}).get("value"):
        seen = com.get("crm_deals_seen") or []
        tail = f": {'; '.join(seen[:3])}" if seen else ""
        lines.append(f"- Known CRM customer (purchase history on record{tail})")
    if f["comments"]:
        parts = [f"\"{e.get('what', '')[:60]}\" ({age_label(e.get('t'), ref)})"
                 for e in f["comments"]]
        lines.append("- Commented on our posts: " + "; ".join(parts))
    if f["promises"]:
        parts = [f"{e.get('what')} ({age_label(e.get('t'), ref)})"
                 for e in f["promises"]]
        lines.append("- OPEN PROMISES we made: " + "; ".join(parts))
    for c in (profile.get("consolidated") or {}).get("stable_facts", [])[:5]:
        lines.append(f"- {c.get('fact')} (since {c.get('since', '?')})")
    for ep in (profile.get("consolidated") or {}).get("episodes", [])[-3:]:
        lines.append(f"- Earlier: {ep.get('what')} ({ep.get('span', '')})")
    linked = profile.get("linked_contacts") or []
    if linked:
        names = ", ".join(f"{l.get('name')} ({l.get('matched_on')})"
                          for l in linked[:3])
        lines.append(f"- Likely same person on other accounts: {names} — "
                     "context only, NEVER reference their other accounts or "
                     "reveal details across them.")
    return "\n".join(lines)


# ── IO: load / save / cold start ────────────────────────────────────────────

async def load(contact_id: int) -> dict | None:
    try:
        contact = await chatwoot.get_contact(int(contact_id))
    except Exception as e:
        print(f"[profile] load failed for contact {contact_id}: {e}")
        return None
    prof = (contact.get("custom_attributes") or {}).get(PROFILE_KEY)
    return prof if isinstance(prof, dict) and prof.get("v") else None


async def save(contact_id: int, profile: dict) -> bool:
    try:
        await chatwoot.update_contact_attributes(
            int(contact_id), {PROFILE_KEY: profile})
        return True
    except Exception as e:
        print(f"[profile] save failed for contact {contact_id}: {e}")
        return False


async def prior_transcript(contact_id: int, current_conv_id=None,
                           max_conversations: int = 3,
                           max_chars: int = 2400) -> str:
    """First-ever sight of a contact: their earlier conversations, rendered
    as a dated transcript for the AGENT to read on its first turn — it
    decides what (if anything) is worth keeping. Nothing is written here."""
    try:
        convs = await chatwoot.get_contact_conversations(int(contact_id))
    except Exception as e:
        print(f"[profile] prior transcript listing failed: {e}")
        return ""
    ref = now()
    blocks = []
    # Oldest first, so a later correction reads as superseding an earlier
    # value; Chatwoot lists newest first.
    ordered = sorted((convs or []), key=lambda c: (c.get("created_at") or c.get("id") or 0))
    for c in ordered:
        cid = c.get("id")
        if not cid or cid == current_conv_id:
            continue
        try:
            msgs = await chatwoot.get_conversation_messages_raw(cid)
        except Exception:
            continue
        lines = []
        is_comment = "comment" in " ".join(c.get("labels") or [])
        for m in msgs or []:
            if m.get("private"):
                continue
            text = (m.get("content") or "").strip()
            cap = msg_attrs(m).get("shared_post_caption")
            if cap and not text:
                text = f"[shared a post: {cap[:120]}]"
            if not text:
                continue
            who = "customer" if m.get("message_type") in (0, "incoming") else "durian"
            lines.append(f"  {who} ({age_label(m.get('created_at') or 0, ref)}): "
                         f"{text[:240]}")
        if lines:
            blocks.append(("Earlier public comments" if is_comment
                           else "Earlier conversation") + ":\n" + "\n".join(lines))
    blocks = blocks[-max_conversations:]          # keep the newest N, in order
    out = "\n".join(blocks)
    return out[-max_chars:] if len(out) > max_chars else out


# ── Soft-linking + CRM lookup ───────────────────────────────────────────────

async def soft_link(profile: dict, contact_id: int) -> None:
    """When a phone is on file and we haven't linked yet, find other Chatwoot
    contacts carrying the same number. Exact-key only; link, never merge."""
    phone = ((profile.get("identity") or {}).get("phone") or {}).get("value")
    if not phone or profile.get("linked_checked"):
        return
    profile["linked_checked"] = True
    digits = re.sub(r"\D", "", phone)[-10:]
    try:
        found = await chatwoot.search_contacts(digits)
    except Exception as e:
        print(f"[profile] soft-link search failed: {e}")
        return
    for c in found or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not cid or int(cid) == int(contact_id):
            continue
        entry = {"id": cid, "name": c.get("name"), "matched_on": "phone",
                 "t": _iso(now())}
        if cid not in [l.get("id") for l in profile.get("linked_contacts") or []]:
            profile.setdefault("linked_contacts", []).append(entry)


async def crm_lookup(profile: dict, conv_id, inbox: str,
                     search_by_phone, get_deals) -> None:
    """Find this customer in Zoho CRM by phone (read-only), remember the CRM
    contact id + a deals snapshot so next time we already know who they are.
    `search_by_phone` / `get_deals` are injected (zoho_crm live, fakes in
    tests). Retries only when a phone exists and no id is stored yet."""
    com = profile.setdefault("commercial", {})
    phone = ((profile.get("identity") or {}).get("phone") or {}).get("value")
    if not phone or (com.get("crm_contact_id") or {}).get("value") \
            or profile.get("crm_checked"):
        return
    profile["crm_checked"] = True   # once per profile until new phone evidence
    try:
        contact = await search_by_phone(phone)
    except Exception as e:
        print(f"[profile] CRM lookup failed: {e}")
        return
    if not contact or not contact.get("id"):
        return
    com["crm_contact_id"] = {"value": str(contact["id"]), "t": _iso(now()),
                             "conv": conv_id, "note": "matched by phone in Zoho CRM"}
    try:
        deals = await get_deals(str(contact["id"]))
    except Exception:
        deals = []
    com["crm_deals_seen"] = [str(d.get("Deal_Name") or d.get("name") or d.get("id"))[:80]
                             for d in (deals or [])[:3]]


# ── Consolidation ───────────────────────────────────────────────────────────

CONSOLIDATE_AFTER_EVENTS = 40
CONSOLIDATE_KEEP_RECENT = 15


def consolidation_due(profile: dict) -> bool:
    return int(profile.get("events_since_consolidation") or 0) >= \
        CONSOLIDATE_AFTER_EVENTS


async def consolidate(profile: dict, llm_summarize) -> bool:
    """Squash all but the newest events into consolidated stable facts /
    episodes / transitions via ONE small model call. `llm_summarize(events)`
    is injected and must return the dict (or None on failure — profile is then
    left untouched; never lose events to a failed summary)."""
    events = profile.get("events") or []
    if len(events) <= CONSOLIDATE_KEEP_RECENT:
        profile["events_since_consolidation"] = 0
        return False
    old, recent = events[:-CONSOLIDATE_KEEP_RECENT], events[-CONSOLIDATE_KEEP_RECENT:]
    try:
        summary = await llm_summarize(old, profile.get("consolidated") or {})
    except Exception as e:
        print(f"[profile] consolidation failed: {e}")
        summary = None
    if not isinstance(summary, dict):
        return False
    cons = profile.setdefault("consolidated", {})
    for k in ("stable_facts", "episodes", "transitions"):
        if isinstance(summary.get(k), list):
            cons[k] = (summary[k])[:12]
    stats = cons.setdefault("stats", {})
    stats["events_consolidated"] = int(stats.get("events_consolidated") or 0) + len(old)
    stats.setdefault("first_seen", old[0].get("t"))
    profile["events"] = recent
    profile["events_since_consolidation"] = 0
    profile["consolidated_at"] = _iso(now())
    return True
