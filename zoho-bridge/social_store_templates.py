# Store-address reply layer for the social store flow. Turns a customer's
# pincode / city / locality (+ product vertical) into the exact store-address
# template to send on Instagram / Facebook.
#
# Resolution order (graceful — always degrades to something correct):
#   pincode → pincode_resolver → a store → that store's LOCATION template
#             → else the CITY template (lists every store in the city)
#   locality named → LOCATION template → else CITY template
#   city named    → CITY template
#   nothing resolvable → None (caller asks for the city)
#
# Names are matched fuzzily (difflib) so sheet-vs-sheet spelling drift
# ("bhubaneshwar" vs "bhubaneswar", "marathalli" vs "marathahalli") still hits.

import difflib
import re
from pathlib import Path

import yaml

import pincode_resolver

_PATH = Path(__file__).parent / "social_store_templates.yaml"
_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        _data = (yaml.safe_load(_PATH.read_text(encoding="utf-8")) or {}).get("verticals") or {}
    return _data


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


def plain(text: str) -> str:
    """Strip `**bold**` markdown the sheet sometimes carries — IG/FB DMs render
    it as literal asterisks. Send the store templates through this."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(text or "")).strip()


# Verticals the templates are keyed by; anything else → furniture (largest net).
_VERTS = {"furniture", "fhc", "doors"}


def _vkey(vertical: str) -> str:
    v = _norm(vertical).replace("full home customisation", "fhc")
    return v if v in _VERTS else "furniture"


def _best(name: str, choices, cutoff=0.8):
    """Exact, then substring, then fuzzy match of `name` among `choices`."""
    if not name or not choices:
        return None
    if name in choices:
        return name
    for c in choices:
        if name in c or c in name:
            return c
    m = difflib.get_close_matches(name, list(choices), n=1, cutoff=cutoff)
    return m[0] if m else None


def _direct(name: str, choices):
    """Exact/substring match only. Used before any cross-city fuzzy search so
    an exact locality such as Goregaon cannot lose to a similar earlier entry
    such as Gurgaon."""
    if not name or not choices:
        return None
    if name in choices:
        return name
    for choice in choices:
        if name in choice or choice in name:
            return choice
    return None


def _city_template(vkey: str, city_key: str) -> str | None:
    c = ((_load().get(vkey) or {}).get("cities") or {}).get(city_key) or {}
    return c.get("template")


def template_for(vertical: str, city: str = "", location: str = "") -> dict | None:
    """Best store template for a (vertical, city, locality). Location template if
    a locality resolves, else the city template. Returns {text, city, location,
    scope} or None."""
    vk = _vkey(vertical)
    cities = (_load().get(vk) or {}).get("cities") or {}
    ckey = _best(_norm(city), cities.keys())
    if not ckey:
        return None
    locs = (cities[ckey].get("locations") or {})
    lkey = _best(_norm(location), locs.keys()) if location else None
    if lkey:
        return {"text": locs[lkey], "city": ckey, "location": lkey, "scope": "location"}
    tpl = cities[ckey].get("template")
    if tpl:
        return {"text": tpl, "city": ckey, "location": "", "scope": "city"}
    # City has only location templates, no city-wide one → use the first location.
    if locs:
        k = sorted(locs)[0]
        return {"text": locs[k], "city": ckey, "location": k, "scope": "location"}
    return None


def _template_for_tag(vkey: str, tag: str) -> dict | None:
    """Furniture pincode tags name a store ('bhubaneshwar - samantarapur',
    'bangalore-marathalli', 'goregaon'). Split into city/locality and resolve;
    a bare locality (no city) is matched against every city's locations."""
    cities = (_load().get(vkey) or {}).get("cities") or {}
    parts = [p for p in re.split(r"\s*-\s*|\s+-\s+", tag) if p.strip()]
    if len(parts) >= 2:
        city_name = _norm(parts[0])
        locality_name = _norm(" ".join(parts[1:]))
        if locality_name == "sn":
            locality_name = "shivaji nagar"
        ckey = _best(city_name, cities.keys())
        if ckey:
            locs = cities[ckey].get("locations") or {}
            lkey = _best(locality_name, locs.keys())
            if lkey:
                return {"text": locs[lkey], "city": ckey,
                        "location": lkey, "scope": "location"}

    # For a bare tag, search ALL cities for an exact locality before allowing
    # any fuzzy match. The old per-city fuzzy loop saw Delhi's "gurgaon" before
    # Mumbai's exact "goregaon" and returned the wrong showroom.
    name = _norm(tag)
    ckey = _direct(name, cities.keys())
    if ckey:
        return template_for(vkey, city=ckey)
    for city_key, city_value in cities.items():
        locs = city_value.get("locations") or {}
        lkey = _direct(name, locs.keys())
        if lkey:
            return {"text": locs[lkey], "city": city_key,
                    "location": lkey, "scope": "location"}

    # No exact city/locality exists: fuzzy-match globally rather than accepting
    # the first vaguely similar locality from the first city in the YAML.
    ckey = _best(name, cities.keys())
    if ckey:
        return template_for(vkey, city=ckey)
    candidates = [(city_key, locality)
                  for city_key, city_value in cities.items()
                  for locality in (city_value.get("locations") or {})]
    fuzzy = difflib.get_close_matches(
        name, [locality for _, locality in candidates], n=1, cutoff=0.8)
    if not fuzzy:
        return None
    matched = fuzzy[0]
    for city_key, locality in candidates:
        if locality == matched:
            text = cities[city_key]["locations"][locality]
            return {"text": text, "city": city_key,
                    "location": locality, "scope": "location"}
    return None


def resolve_store_reply(vertical: str, *, pincode=None, city: str = "",
                        location: str = "") -> dict | None:
    """The single entry point for the DM flow. Returns
    {text, store, city, location, scope, how} or None (→ ask for the city).
      how: 'pincode:<mode>' | 'locality' | 'city'
    """
    vk = _vkey(vertical)

    # 1. Pincode → nearest/tagged store → its template.
    if pincode:
        r = pincode_resolver.resolve(pincode, vk)
        if r:
            got = (template_for(vk, city=r.get("city", ""), location=r["store"])
                   if r.get("city") else _template_for_tag(vk, _norm(r["store"])))
            if got:
                return {**got, "store": r["store"], "how": f"pincode:{r['mode']}"}
            # resolver placed it but we couldn't map to a template → fall through.

    # 2/3. Locality or city named directly.
    if location or city:
        got = template_for(vk, city=city, location=location)
        if got:
            return {**got, "store": got.get("location") or got["city"],
                    "how": "locality" if got["scope"] == "location" else "city"}
    return None
