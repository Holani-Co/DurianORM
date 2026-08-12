# Customer profile — the durable, per-contact memory behind agent mode.
#
# Shape: an append-only EVENT LOG (exact timestamps + source message ids) plus
# a small consolidated section for aged-out history. Everything the model sees
# is FOLDED from events at read time, so a later "no" outranks an earlier
# "yes" purely by time. Stored on the Chatwoot CONTACT record
# (custom_attributes.durian_profile) so it follows the person across
# conversations and is visible/editable in the Chatwoot sidebar.
#
# Learning lanes:
#   lane 1 (code, every turn)  — regex/lookup facts: phone, pincode, catalog
#       family products, shared-post captions, comments, routing/deal attrs.
#   lane 2 (model, same call)  — judgment events (declined / preference /
#       correction / promise) proposed in finish.learned and accepted ONLY if
#       their `quote` appears in this turn's customer messages. The model can
#       never dream a fact into someone's permanent record.
#
# Size: the rendered block is budgeted (~500 tokens); events compact into
# `consolidated` via a write-triggered pass (maybe_consolidate) — no cron,
# dormant contacts cost nothing.

import json
import re
from datetime import datetime, timedelta, timezone

import chatwoot
import product_catalog

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


def empty_profile() -> dict:
    return {"v": 1, "updated_at": _iso(now()), "consolidated_at": None,
            "events_since_consolidation": 0, "identity": {}, "location": {},
            "commercial": {}, "linked_contacts": [], "events": [],
            "consolidated": {"stable_facts": [], "episodes": [],
                             "transitions": [], "stats": {}}}


# ── Lane 1: deterministic event extraction ──────────────────────────────────

def events_from_conversation(conv: dict, messages: list,
                             after_msg_id: int | None = None) -> list[dict]:
    """Code-derived events from one conversation's messages (newer than
    after_msg_id when given). Product detection is family-anchored only."""
    conv_id = conv.get("id")
    inbox = ((conv.get("meta") or {}).get("channel") or "") or \
        (conv.get("inbox") or {}).get("name") or ""
    is_comment = "comment" in " ".join(conv.get("labels") or [])
    out: list[dict] = []
    for m in messages or []:
        mid = m.get("id")
        if after_msg_id is not None and (mid or 0) <= after_msg_id:
            continue
        if m.get("message_type") not in (0, "incoming"):
            continue
        text = (m.get("content") or "").strip()
        t = _ts(m.get("created_at"))
        base = {"t": t, "msg": mid, "conv": conv_id, "inbox": inbox}
        cap = (m.get("content_attributes") or {}).get("shared_post_caption")
        if cap:
            out.append({**base, "kind": "shared_post", "what": cap[:160]})
        if not text:
            continue
        if is_comment:
            out.append({**base, "kind": "commented", "what": text[:160]})
            continue
        pm = _PHONE_RE.search(text)
        if pm:
            out.append({**base, "kind": "phone", "what": pm.group(0)})
        pinm = _PIN_RE.search(text)
        if pinm and not pm:  # a phone contains 6-digit runs; phone wins
            out.append({**base, "kind": "pincode", "what": pinm.group(0)})
        hit = product_catalog.search(text, limit=1, require_family=True)
        if hit:
            out.append({**base, "kind": "interest",
                        "what": hit[0].get("name") or hit[0].get("sku"),
                        "sku_family": hit[0].get("family")})
    # Conversation-level facts the gates/deal flow wrote.
    ca = conv.get("custom_attributes") or {}
    conv_t = _ts(conv.get("last_activity_at") or conv.get("created_at"))
    owner = ca.get("retail_deal_owner") or {}
    if owner.get("location"):
        out.append({"t": conv_t, "msg": None, "conv": conv_id, "inbox": inbox,
                    "kind": "routed", "what": owner.get("location"),
                    "city": owner.get("city")})
    if ca.get("crm_deal_id"):
        out.append({"t": conv_t, "msg": None, "conv": conv_id, "inbox": inbox,
                    "kind": "deal_created", "what": str(ca["crm_deal_id"])})
    return out


# ── Lane 2: quote-verified model learnings ──────────────────────────────────

_LEARN_KINDS = {"declined", "preference", "correction", "budget", "objection",
                "promise", "note"}


def verify_learned(learned: list, incoming_texts: list[str],
                   conv_id, inbox: str) -> list[dict]:
    """Accept a model-proposed learning only when its quote actually appears in
    this turn's customer messages (normalised substring)."""
    haystack = re.sub(r"\s+", " ", " ".join(incoming_texts or [])).lower()
    out = []
    for item in learned or []:
        kind = str(item.get("kind") or "").strip().lower()
        quote = re.sub(r"\s+", " ", str(item.get("quote") or "")).strip().lower()
        what = str(item.get("what") or item.get("new") or "").strip()
        if kind not in _LEARN_KINDS or not what:
            continue
        if not quote or quote[:80] not in haystack:
            print(f"[profile] rejected unverified learning: {kind}/{what[:40]}")
            continue
        ev = {"t": _iso(now()), "msg": None, "conv": conv_id, "inbox": inbox,
              "kind": kind, "what": what[:160], "quote": quote[:160]}
        if item.get("field"):
            ev["field"] = str(item["field"])[:40]
        out.append(ev)
    return out


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
        kind, what = ev.get("kind"), ev.get("what")
        src = {"t": ev["t"], "conv": ev.get("conv")}
        if kind == "phone":
            profile.setdefault("identity", {})["phone"] = {"value": what, **src}
        elif kind == "pincode":
            profile.setdefault("location", {})["pincode"] = {"value": what, **src}
        elif kind == "routed":
            profile.setdefault("commercial", {})["showroom"] = {"value": what, **src}
            if ev.get("city"):
                profile.setdefault("location", {})["city"] = {"value": ev["city"], **src}
        elif kind == "deal_created":
            profile.setdefault("commercial", {}).setdefault("deals", [])
            if what not in [d.get("id") for d in profile["commercial"]["deals"]]:
                profile["commercial"]["deals"].append({"id": what, **src})
        elif kind == "crm_linked":
            profile.setdefault("commercial", {})["crm_contact_id"] = {"value": what, **src}
        elif kind == "correction" and ev.get("field") in ("city", "pincode"):
            profile.setdefault("location", {})[ev["field"]] = {"value": what, **src}
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
            lines.append(f"- {label}: {d['value']} ({age_label(d.get('t'), ref)})")
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
        lines.append("- Known CRM customer (purchase history on record)")
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


async def cold_start(contact_id: int, current_conv_id=None,
                     max_conversations: int = 8) -> dict:
    """First-ever sight of this contact: scan their conversation history once
    (comments included) and build the initial profile."""
    profile = empty_profile()
    try:
        convs = await chatwoot.get_contact_conversations(int(contact_id))
    except Exception as e:
        print(f"[profile] cold start listing failed: {e}")
        return profile
    for c in (convs or [])[:max_conversations]:
        cid = c.get("id")
        if not cid:
            continue
        try:
            msgs = await chatwoot.get_conversation_messages_raw(cid)
        except Exception:
            continue
        merge_events(profile, events_from_conversation(c, msgs))
    ident_name = None
    for c in (convs or []):
        ident_name = ((c.get("meta") or {}).get("sender") or {}).get("name")
        if ident_name:
            break
    if ident_name:
        profile.setdefault("identity", {})["name"] = {"value": ident_name,
                                                      "t": _iso(now())}
    return profile


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
    merge_events(profile, [{"t": _iso(now()), "msg": None, "conv": conv_id,
                            "inbox": inbox, "kind": "crm_linked",
                            "what": str(contact["id"])}])
    try:
        deals = await get_deals(str(contact["id"]))
    except Exception:
        deals = []
    for d in (deals or [])[:3]:
        merge_events(profile, [{
            "t": _iso(now()), "msg": None, "conv": conv_id, "inbox": inbox,
            "kind": "note",
            "what": f"CRM deal on record: {str(d.get('Deal_Name') or d.get('name') or d.get('id'))[:80]}"}])


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
