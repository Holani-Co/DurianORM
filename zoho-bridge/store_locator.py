# The one store/showroom resolver. Everything store-address goes through here —
# the IG/FB agent and the WhatsApp flows both call resolve(), so there is a
# single, vertical-scoped source of truth instead of per-channel/per-vertical
# logic that leaked furniture into doors/FHC.
#
# Two layers:
#   FLOOR    = data/store_registry.json (built from the client's export by
#              build_store_registry.py).
#   OVERRIDE = config_store domain "stores" — what the client edits in Settings;
#              per-store field edits + a `disabled` flag + optional radius_km.
# A missing/broken override falls back to the floor, so a bad edit can't break
# the store path.
#
# resolve(vertical, pincode|city) considers ONLY stores of that vertical (a
# store's `verticals` = its own vertical + any it "Services"), returns the
# nearest one within the per-vertical radius, else None → caller tells the
# customer there's no store nearby and captures the lead. Never cross-vertical,
# never a far store presented as theirs.

import json
from pathlib import Path

import config
import config_store
import pincode_resolver

_REGISTRY = Path(__file__).parent / "data" / "store_registry.json"
_floor_cache: list | None = None

# Verticals the locator knows, and the store fields the Settings editor may
# override (everything the customer can receive, plus coords/owner for routing).
VERTICALS = ("furniture", "doors", "fhc")
EDITABLE_FIELDS = ("card_name", "address", "city", "pincode", "manager", "phone",
                   "email", "timing", "map_url", "crm_owner_id", "lat", "lon")


def _floor() -> list:
    global _floor_cache
    if _floor_cache is None:
        try:
            _floor_cache = json.loads(_REGISTRY.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"[store-locator] registry load failed: {e}")
            _floor_cache = []
    return _floor_cache


def reload() -> None:
    """Drop the floor cache (call after regenerating the registry)."""
    global _floor_cache
    _floor_cache = None


def floor_ids() -> set:
    """Store ids in the registry floor — the valid targets for an override."""
    return {s["id"] for s in _floor()}


def all_stores() -> list:
    """Floor merged with the active override — the effective store list the
    Settings editor shows."""
    return _stores()


def _override_doc() -> dict:
    return config_store.get_active_override(config_store.STORES) or {}


def _radius_km(vertical: str) -> float:
    doc = _override_doc().get("radius_km") or {}
    try:
        return float(doc[vertical])
    except (KeyError, TypeError, ValueError):
        return float(config.STORE_LOCATOR_RADIUS_KM.get(vertical, 150.0))


def _stores() -> list:
    """Floor merged with the active per-store overrides; disabled stores dropped."""
    edits = _override_doc().get("stores") or {}
    out = []
    for s in _floor():
        o = edits.get(s["id"]) if isinstance(edits, dict) else None
        if isinstance(o, dict):
            if o.get("disabled"):
                continue
            s = {**s, **{k: v for k, v in o.items() if k != "disabled"}}
        out.append(s)
    return out


def _result(store: dict, distance_km) -> dict:
    return {
        "id": store["id"],
        "name": store["name"],
        "card_name": store.get("card_name") or store["name"],
        "city": store.get("city"),
        "distance_km": distance_km,
        "address": store.get("address") or "",
        "pincode": store.get("pincode") or "",
        "manager": store.get("manager") or "",
        "phone": store.get("phone") or "",
        "email": store.get("email") or "",
        "timing": store.get("timing") or "",
        "map_url": store.get("map_url") or "",
        "crm_owner_id": store.get("crm_owner_id") or "",
    }


def _nearest_in_range(stores: list, loc, radius: float):
    """(store, distance_km) of the nearest store with coords within `radius`, or
    (None, None)."""
    best = best_d = None
    for s in stores:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        d = pincode_resolver.haversine_km(loc[0], loc[1], s["lat"], s["lon"])
        if best_d is None or d < best_d:
            best, best_d = s, d
    if best is None or best_d > radius:
        return None, None
    return best, round(best_d, 1)


def resolve(vertical: str, pincode=None, city: str = None) -> dict | None:
    """Nearest serviceable store of `vertical` for the customer's pincode (or
    city). Returns a result dict, None (nothing in range → capture the lead), or
    {"ambiguous": True, "options": [...]} when a city alone matches several and
    we should ask for a pincode.

    A store whose PRIMARY vertical matches is preferred over one that only
    "Services" the vertical (a dedicated FHC studio beats a doors store that also
    does FHC); the services-it stores are the fallback when no dedicated one is
    in range."""
    vert = (vertical or "").strip().lower()
    candidates = [s for s in _stores() if vert in (s.get("verticals") or [])]
    if not candidates:
        return None
    primary = [s for s in candidates if s.get("primary_vertical") == vert]
    service = [s for s in candidates if s.get("primary_vertical") != vert]

    pin = pincode_resolver.normalize_pincode(pincode) if pincode else None
    if pin:
        # Exact-pincode match first — the customer is literally at a store's
        # pincode, which also covers pincodes the generic geocoder doesn't carry.
        exact = sorted((s for s in candidates if s.get("pincode") == pin),
                       key=lambda s: 0 if s.get("primary_vertical") == vert else 1)
        if exact:
            return _result(exact[0], 0.0)
        loc = pincode_resolver.coords(pin)
        if not loc:
            return None                       # can't place the pincode → caller asks
        radius = _radius_km(vert)
        for tier in (primary, service):       # dedicated-vertical stores win
            best, dist = _nearest_in_range(tier, loc, radius)
            if best:
                return _result(best, dist)
        return None                           # none in range → not serviceable

    if city:
        key = city.strip().lower()
        matches = [s for s in candidates if key and key in (s.get("city") or "").lower()]
        if not matches:
            return None
        matches = [s for s in matches if s.get("primary_vertical") == vert] or matches
        if len(matches) == 1:
            return _result(matches[0], None)
        return {"ambiguous": True, "vertical": vert,
                "options": [m.get("card_name") or m["name"] for m in matches]}
    return None


def format_card(result: dict) -> str:
    """Customer-facing address card (facts only). The agent reframes it in its
    own voice; the deterministic WhatsApp flow sends it as-is."""
    r = result
    lines = [f"*{r['card_name']}*"]
    for icon, key in (("📍", "address"), ("🕒", "timing"),
                      ("👤", "manager"), ("📞", "phone"), ("🗺️", "map_url")):
        if r.get(key):
            lines.append(f"{icon} {r[key]}")
    return "\n".join(lines)
