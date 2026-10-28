# Zoho owner sync. Every owner the ORM hands a deal to — the routing config's
# crm_owner_routing_* maps and each store's crm_owner_id (Settings → Stores) — is
# a Zoho CRM user id. The client deactivates, deletes and renames users without
# telling us, and a deal for a dead id fails outright with INVALID_DATA on
# $.data[0].Owner.id (convs 7148 / 7604 / 9266 / 11191).
#
# A daily snapshot of Zoho's users (active + deactivated) lets the bridge:
#   1. swap a dead owner at deal time for a live one (live_owner) — a same-named
#      successor ("Delhi Private Project_" → "Delhi Private Project"), else the
#      dead user's reporting manager, else the caller's vertical fallback;
#   2. list every dead owner with its suggested successor (audit) for the
#      Settings → Routing config panel and the daily alert email.
# Read-only in Zoho, and it never rewrites the routing or stores config — a
# person picks the permanent replacement.
import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import classifier
import config
import store_locator
import zoho_crm

IST = ZoneInfo("Asia/Kolkata")
_STATE_PATH = os.environ.get(
    "OWNER_SYNC_STATE_PATH", os.path.join(os.path.dirname(__file__), "owner_sync_state.json"))

# {"at": iso, "active": {id: user}, "inactive": {id: user}, "names": {id: name}}
# `names` remembers every name ever seen (plus names recovered from records), so
# a DELETED user — absent from both Zoho lists — can still be matched by name.
_state: dict = {}
_lock = asyncio.Lock()

# Suffixes Zoho admins add when they retire a user ("Delhi Private Project_",
# "Indore Project (old)", "Raj Singh - inactive").
_RETIRED_WORDS = {"old", "inactive", "deactivated", "left", "ex", "former"}


def _load() -> dict:
    global _state
    if not _state:
        try:
            with open(_STATE_PATH, encoding="utf-8") as f:
                _state = json.load(f)
        except (OSError, ValueError):
            _state = {}
    return _state


def _save() -> None:
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f)
    os.replace(tmp, _STATE_PATH)


def _norm_name(name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    while words and words[-1] in _RETIRED_WORDS:
        words.pop()
    return " ".join(words)


def _user(u: dict) -> dict:
    mgr = u.get("Reporting_To") or {}
    return {"id": str(u.get("id") or ""), "name": (u.get("full_name") or "").strip(),
            "email": (u.get("email") or "").strip(),
            "reports_to": str(mgr.get("id") or "") if isinstance(mgr, dict) else ""}


async def _list_users(kind: str) -> dict:
    out, page = {}, 1
    while page <= 20:
        resp = await zoho_crm._crm_request("GET", "/users", params={
            "type": kind, "page": page, "per_page": 200})
        for u in resp.get("users") or []:
            user = _user(u)
            if user["id"]:
                out[user["id"]] = user
        if not (resp.get("info") or {}).get("more_records"):
            break
        page += 1
    return out


async def refresh() -> dict:
    """Re-read Zoho's users into the snapshot. Raises on a CRM error (missing
    users.READ scope, network) and keeps the previous snapshot."""
    async with _lock:
        active = await _list_users("ActiveUsers")
        inactive = await _list_users("DeactiveUsers")
        state = _load()
        names = dict(state.get("names") or {})
        for u in (*active.values(), *inactive.values()):
            if u["name"]:
                names[u["id"]] = u["name"]
        state.update(at=datetime.now(IST).isoformat(timespec="seconds"),
                     active=active, inactive=inactive, names=names)
        # A deleted owner's name is only on its old records — look it up once.
        for oid in referenced_ids():
            if oid not in active and oid not in inactive and oid not in names:
                name = await _name_from_records(oid)
                if name:
                    names[oid] = name
        _save()
        print(f"[owner-sync] {len(active)} active / {len(inactive)} deactivated Zoho users")
        return state


async def _name_from_records(oid: str) -> str:
    for module in ("Deals", "Contacts"):
        try:
            resp = await zoho_crm._crm_request("GET", f"/{module}/search", params={
                "criteria": f"(Owner:equals:{oid})", "per_page": 1, "fields": "Owner"})
            owner = ((resp.get("data") or [{}])[0]).get("Owner") or {}
            if owner.get("name"):
                return owner["name"]
        except Exception as e:  # noqa: BLE001
            print(f"[owner-sync] name lookup for {oid} in {module} failed: {e}")
    return ""


def status(owner_id) -> str:
    """'active' | 'deactivated' | 'deleted' | 'unknown' (no snapshot yet)."""
    s = _load()
    oid = str(owner_id or "")
    if not s.get("active"):
        return "unknown"
    if oid in s["active"]:
        return "active"
    return "deactivated" if oid in (s.get("inactive") or {}) else "deleted"


def user(owner_id) -> dict:
    s, oid = _load(), str(owner_id or "")
    u = (s.get("active") or {}).get(oid) or (s.get("inactive") or {}).get(oid) or {}
    return {"id": oid, "name": u.get("name") or (s.get("names") or {}).get(oid, ""),
            "email": u.get("email", "")}


def successor(owner_id) -> dict | None:
    """A live replacement for a dead owner, or None: the one active user with the
    same name (Zoho admins hand the role name to the new user), else the dead
    user's reporting manager when that manager is active."""
    s, oid = _load(), str(owner_id or "")
    active = s.get("active") or {}
    name = _norm_name(user(oid)["name"])
    if name:
        same = [u for u in active.values() if _norm_name(u["name"]) == name]
        if len(same) == 1:
            return {**same[0], "reason": "same Zoho user name"}
    mgr = ((s.get("inactive") or {}).get(oid) or {}).get("reports_to")
    if mgr and mgr in active:
        return {**active[mgr], "reason": "their reporting manager"}
    return None


def live_owner(owner: dict, fallback: dict) -> tuple[dict, str]:
    """The owner to actually put on the deal. A live (or not-yet-known) owner is
    kept; a dead one is swapped for its successor, else `fallback`. Returns
    (owner, note) — note is "" when nothing changed."""
    oid = str((owner or {}).get("owner_id") or "")
    st = status(oid)
    if not oid or st in ("active", "unknown"):
        return owner, ""
    old = user(oid)
    who = old["name"] or old["email"] or (owner or {}).get("owner_email") or oid
    nxt = successor(oid)
    if nxt:
        new = {**owner, "owner_id": nxt["id"], "owner_email": nxt["email"]}
        return new, (f"Deal owner {who} is {st} in Zoho — assigned to {nxt['name']} "
                     f"({nxt['email']}, {nxt['reason']}).")
    return fallback, (f"Deal owner {who} is {st} in Zoho — assigned to the fallback "
                      f"owner {fallback.get('owner_email') or fallback.get('owner_id')}.")


# ── What the ORM references ────────────────────────────────────────────────

def _walk(node, path):
    if isinstance(node, dict):
        if "owner_id" in node:
            yield path, node
        for k, v in node.items():
            if k not in ("owner_id", "owner_email"):
                yield from _walk(v, path + [str(k)])
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + [f"#{i + 1}"] if len(node) > 1 else path)


def references() -> list[dict]:
    """Every owner slot: routing-config owners (the crm_owner_routing_* maps,
    minus the parked retail matrix) and each enabled store's CRM owner."""
    refs = []
    for key, node in (classifier.get_routing_rules() or {}).items():
        if not key.startswith("crm_owner_routing") or key == "crm_owner_routing":
            continue
        group = key.replace("crm_owner_routing_", "").replace("_", " ")
        for path, entry in _walk(node, []):
            if entry.get("owner_id"):
                refs.append({"kind": "routing", "group": group,
                             "label": " · ".join(path) or group,
                             "owner_id": str(entry["owner_id"]),
                             "owner_email": entry.get("owner_email") or ""})
    for s in store_locator._stores():
        if s.get("crm_owner_id"):
            refs.append({"kind": "store", "group": "stores", "label": s.get("name") or s["id"],
                         "owner_id": str(s["crm_owner_id"]), "owner_email": s.get("email") or ""})
    return refs


def referenced_ids() -> set:
    return {r["owner_id"] for r in references()}


def audit() -> dict:
    """Every owner slot whose Zoho user is no longer active, with its suggested
    successor — for Settings and the daily alert."""
    s = _load()
    problems = []
    for r in references():
        st = status(r["owner_id"])
        if st in ("active", "unknown"):
            continue
        old = user(r["owner_id"])
        problems.append({**r, "status": st, "owner_name": old["name"],
                         "owner_email": old["email"] or r["owner_email"],
                         "successor": successor(r["owner_id"])})
    return {"synced_at": s.get("at"), "ready": bool(s.get("active")),
            "active_users": len(s.get("active") or {}), "problems": problems}


def active_owner_options() -> list[dict]:
    """Active Zoho users as {owner_email, owner_id, name} for the owner pickers."""
    return sorted(({"owner_email": u["email"], "owner_id": u["id"], "name": u["name"]}
                   for u in (_load().get("active") or {}).values() if u["email"]),
                  key=lambda o: o["owner_email"].lower())


# ── Daily loop ─────────────────────────────────────────────────────────────

def _seconds_until_next_run() -> float:
    now = datetime.now(IST)
    target = now.replace(hour=config.OWNER_SYNC_HOUR, minute=config.OWNER_SYNC_MINUTE,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_forever():
    """Refresh at boot, then daily (before the 9:15 IST alert email). Boot-safe:
    a CRM error is logged and the last snapshot stays in use."""
    if not config.ZOHO_CRM_ENABLED:
        return
    while True:
        try:
            await refresh()
            n = len(audit()["problems"])
            if n:
                print(f"[owner-sync] {n} owner slot(s) point at inactive Zoho users")
        except Exception as e:  # noqa: BLE001
            print(f"[owner-sync] refresh failed: {e}")
        await asyncio.sleep(_seconds_until_next_run())
