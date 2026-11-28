# The one store/showroom resolver. Everything store-address goes through here —
# the IG/FB agent and the WhatsApp flows both call resolve(), so there is a
# single, vertical-scoped source of truth instead of per-channel/per-vertical
# logic that leaked furniture into doors/FHC.
#
# Two layers:
#   FLOOR    = data/store_registry.json (built from the client's export by
#              build_store_registry.py).
#   OVERRIDE = config_store domain "stores" — what the client edits in Settings;
#              per-store field edits + a `disabled` flag + optional radius_km,
#              plus `new_stores`: stores the client added in Settings (an
#              upcoming store that has opened). They join the floor, so they
#              resolve, route deals and can be edited/disabled like any other.
# A missing/broken override falls back to the floor, so a bad edit can't break
# the store path.
#
# resolve(vertical, pincode|city) considers ONLY stores of that vertical (a
# store's `verticals` = its own vertical + any it "Services"), returns the
# nearest one within the per-vertical radius, else None → caller tells the
# customer there's no store nearby and captures the lead. Never cross-vertical,
# never a far store presented as theirs.

import json
import re
from pathlib import Path

import config
import config_store
import pincode_resolver
import store_maps

_REGISTRY = Path(__file__).parent / "data" / "store_registry.json"
_floor_cache: list | None = None

# Place matching. Customers name a LOCALITY ("Goregaon", "Kirti Nagar", "JP
# Nagar") as often as a city, so a place term is matched against a store's
# name + location + city, not the city field alone. Matching is exact on whole
# words (every word of the place must appear in the store's words) — never fuzzy,
# so "Goregaon" can't drift to "Gurgaon". Old/alternate city names are folded to
# the registry's spelling on both sides.
_PLACE_ALIASES = {
    "bangalore": "bengaluru", "gurgaon": "gurugram", "bombay": "mumbai",
    "calcutta": "kolkata", "madras": "chennai", "allahabad": "prayagraj",
    "poona": "pune", "baroda": "vadodara", "mysore": "mysuru",
    "trivandrum": "thiruvananthapuram", "vizag": "visakhapatnam",
    "cochin": "kochi", "pondicherry": "puducherry", "benares": "varanasi",
    "banaras": "varanasi", "bhubaneshwar": "bhubaneswar", "bhubaneshwer": "bhubaneswar",
}
# Words that describe the brand/store, not the place.
_PLACE_STOPWORDS = {
    "durian", "furniture", "door", "doors", "fhc", "full", "home", "customisation",
    "customization", "showroom", "showrooms", "store", "stores", "studio", "outlet",
    "experience", "centre", "center", "any", "a", "an", "the", "in", "near", "at",
    "of", "and", "new", "city",
    # Directions customers add to a locality ("Thane West", "Andheri East").
    "east", "west", "north", "south",
}


def _place_words(text) -> set:
    words = set()
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        w = _PLACE_ALIASES.get(w, w)
        if w not in _PLACE_STOPWORDS:
            words.add(w)
    return words


def _store_words(s: dict) -> set:
    return _place_words(" ".join(str(s.get(k) or "") for k in
                                 ("name", "card_name", "location", "city")))


def _match_place(candidates: list, place: str, vert: str) -> list:
    """Stores (of this vertical) whose name/location/city contain every word of
    `place`. A store whose PRIMARY vertical matches is preferred over one that
    only services it."""
    q = _place_words(place)
    if not q:
        return []
    hits = [s for s in candidates if q <= _store_words(s)]
    if not hits:
        # An extra word no store carries ("Thane West Mumbai": no store is named
        # "West") must not hide the store the other words name. Fall back to the
        # most specific word that does name stores: "thane" (one store) beats
        # "mumbai" (two). Several stores left → the caller asks which one.
        per_word = [h for h in ([s for s in candidates if w in _store_words(s)] for w in q) if h]
        hits = min(per_word, key=len) if per_word else []
    return [s for s in hits if s.get("primary_vertical") == vert] or hits


def knows_place(place: str) -> bool:
    """The place names at least one Durian store's city or area, of ANY line —
    so "no store of this line there" is a real answer. False for a locality we
    cannot place at all ("Kandivali West")."""
    return bool(_match_place(_stores(), place, ""))


def match_store(vertical: str, text: str) -> dict | None:
    """The one store of `vertical` the customer picked by name — an option we
    listed ("Full Home Customisation - Thane - Subhash Nagar") or just its area
    ("Thane"). None when it names no store or still several."""
    vert = (vertical or "").strip().lower()
    candidates = [s for s in _stores() if vert in (s.get("verticals") or [])]
    want = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    exact = [s for s in candidates
             if want and want in ((s.get("card_name") or "").casefold(), s["name"].casefold())]
    hits = exact or (_match_place(candidates, text, vert) if want else [])
    return _result(hits[0], None) if len(hits) == 1 else None

# Verticals the locator knows, and the store fields the Settings editor may
# override (everything the customer can receive, plus coords/owner for routing).
VERTICALS = ("furniture", "doors", "fhc")
EDITABLE_FIELDS = ("card_name", "address", "city", "pincode", "manager", "phone",
                   "email", "timing", "map_url", "crm_owner_id", "lat", "lon",
                   # Master ORM list extras: not printed on the card; the agent
                   # mentions one when the customer asks or client guidance says to.
                   "holiday", "parking", "store_type", "area", "floors",
                   "escalator")


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


def registry_ids() -> set:
    """Store ids in the registry file alone (not the ones added in Settings)."""
    return {s["id"] for s in _floor()}


def floor_ids() -> set:
    """Every store id an edit can target: the registry plus stores added in Settings."""
    return registry_ids() | {s["id"] for s in _added_stores()}


# A store added in Settings carries the editable fields plus its id and vertical.
NEW_STORE_REQUIRED = ("id", "vertical", "card_name", "address", "city", "pincode")


def _added_stores(doc: dict | None = None) -> list:
    """The override's `new_stores` in the registry's own shape. With no
    coordinates given, a store sits at its pincode's centre — close enough for
    "nearest store", and its exact pincode and name match it anyway."""
    out = []
    raw = (doc if doc is not None else _override_doc()).get("new_stores") or []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict) or not all(str(e.get(k) or "").strip()
                                               for k in NEW_STORE_REQUIRED):
            continue
        vert = str(e["vertical"]).strip().lower()
        s = {k: e[k] for k in EDITABLE_FIELDS if e.get(k) not in (None, "")}
        s.update(id=str(e["id"]).strip(), name=str(e["card_name"]).strip(),
                 verticals=[vert], primary_vertical=vert, added_in_settings=True)
        if s.get("lat") is None or s.get("lon") is None:
            loc = pincode_resolver.coords(s.get("pincode"))
            if loc:
                s["lat"], s["lon"] = loc
        out.append(s)
    return out


def all_stores() -> list:
    """Floor merged with the active override — the effective store list the
    Settings editor shows."""
    return _stores()


def store_for_owner(owner_id, owner_name: str = "") -> dict | None:
    """The store whose CRM owner is this Zoho user, or None. When one login owns
    several stores (FHC Hyderabad also owns the Kompally furniture showroom),
    the store whose name shares the most words with the owner's name wins."""
    oid = str(owner_id or "")
    mine = [s for s in _stores() if oid and str(s.get("crm_owner_id") or "") == oid]
    if not mine:
        return None
    words = set(re.findall(r"[a-z]+", (owner_name or "").lower()))
    return max(mine, key=lambda s: (len(words & set(re.findall(r"[a-z]+", (s.get("name") or "").lower()))),
                                    bool(s.get("phone"))))


def upcoming(vertical: str, pincode=None, city: str = None) -> dict | None:
    """An enabled 'upcoming store' entry matching the customer's location for this
    vertical, or None. Client-managed in the stores override under `upcoming`:
    [{vertical, area, pincodes?, note?, enabled}]. Matched by area name
    (case-insensitive, either direction) or an exact pincode in the entry's list.
    Lets the bot answer 'a store is opening soon there' instead of 'none nearby'."""
    vert = (vertical or "").strip().lower()
    pin = pincode_resolver.normalize_pincode(pincode) if pincode else None
    c = (city or "").strip().lower()
    for e in _override_doc().get("upcoming") or []:
        if not isinstance(e, dict) or not e.get("enabled", True):
            continue
        if (e.get("vertical") or "").strip().lower() != vert:
            continue
        area = (e.get("area") or "").strip().lower()
        matched = (c and area and (area in c or c in area)) or (
            pin and pin in [pincode_resolver.normalize_pincode(p)
                            for p in (e.get("pincodes") or [])])
        if matched:
            return {"area": e.get("area") or "", "note": e.get("note") or "",
                    "vertical": vert}
    return None


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
    for s in _floor() + _added_stores():
        o = edits.get(s["id"]) if isinstance(edits, dict) else None
        if isinstance(o, dict) and o.get("disabled"):
            continue
        # The store's own Google listing link beats the sheet's (which reused one
        # link per building); a map_url set in Settings still wins over both.
        # A store added in Settings too (ISS-634: FHC Bikaner was given a
        # share.google link, which Instagram shows as plain text, not a map
        # preview); the link typed in its form is the fallback.
        gbp = store_maps.link_for(s)
        if gbp:
            s = {**s, "map_url": gbp}
        if isinstance(o, dict):
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
        # Extras (see EDITABLE_FIELDS) — format_card never prints them.
        "holiday": store.get("holiday") or "",
        "parking": store.get("parking") or "",
        "store_type": store.get("store_type") or "",
        "area": store.get("area") or "",
        "floors": store.get("floors") or "",
        "escalator": store.get("escalator") or "",
    }


def _nearest_in_range(stores: list, loc, radius: float):
    """(store, distance_km) of the nearest store with coords within `radius`, or
    (None, None). A store without real coordinates is skipped here (it still
    matches its exact pincode and its name) — never placed at a pincode centroid,
    which for a metro is one point shared by most of its pincodes."""
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


def resolve(vertical: str, pincode=None, city: str = None,
            primary_only: bool = False) -> dict | None:
    """Nearest serviceable store of `vertical` for the customer's pincode (or
    city). Returns a result dict, None (nothing in range → capture the lead), or
    {"ambiguous": True, "options": [...]} when a city alone matches several and
    we should ask for a pincode.

    A store whose PRIMARY vertical matches is preferred over one that only
    "Services" the vertical (a dedicated FHC studio beats a doors store that also
    does FHC); the services-it stores are the fallback when no dedicated one is
    in range. `primary_only` drops them entirely — deal OWNERSHIP stays inside
    the vertical (a furniture/doors store that also services FHC never owns an
    FHC deal)."""
    # The model sometimes passes a dict/number for pincode or city — coerce to a
    # safe string so a malformed tool arg degrades to "not given", never crashes.
    vertical = vertical if isinstance(vertical, str) else ""
    pincode = pincode if isinstance(pincode, str) else ""
    city = city if isinstance(city, str) else ""
    vert = vertical.strip().lower()
    candidates = [s for s in _stores() if vert in (s.get("verticals") or [])]
    if primary_only:
        candidates = [s for s in candidates if s.get("primary_vertical") == vert]
    if not candidates:
        return None
    primary = [s for s in candidates if s.get("primary_vertical") == vert]
    service = [s for s in candidates if s.get("primary_vertical") != vert]

    # A place the customer NAMED — a locality ("Goregaon") or a city. When it pins
    # exactly ONE store it is their explicit ask this turn, so it wins even over a
    # pincode (which may be a stale one carried in their profile).
    place_hits = _match_place(candidates, city, vert) if city.strip() else []
    if len(place_hits) == 1:
        return _result(place_hits[0], None)

    pin = pincode_resolver.normalize_pincode(pincode) if pincode else None
    if pin:
        # Exact-pincode match first — the customer is literally at a store's
        # pincode, which also covers pincodes the generic geocoder doesn't carry.
        exact = sorted((s for s in candidates if s.get("pincode") == pin),
                       key=lambda s: 0 if s.get("primary_vertical") == vert else 1)
        if exact:
            return _result(exact[0], 0.0)
        loc = pincode_resolver.coords(pin)
        if loc:
            radius = _radius_km(vert)
            for tier in (primary, service):   # dedicated-vertical stores win
                best, dist = _nearest_in_range(tier, loc, radius)
                if best:
                    return _result(best, dist)
        # The pincode found nothing in range (or can't be placed) → fall through
        # to the place they named, so a stale pincode never masks it.

    if len(place_hits) > 1:                   # e.g. "Mumbai" → ask for a pincode
        return {"ambiguous": True, "vertical": vert,
                "options": [m.get("card_name") or m["name"] for m in place_hits]}
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
