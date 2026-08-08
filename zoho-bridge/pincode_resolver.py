# Pincode → showroom resolver for the social store-template flow. Pure stdlib —
# no geocoding at runtime: it reads the pre-built data in ./data (regenerate with
# build_pincode_data.py when the client updates the sheets).
#
# Two modes, because the client's pincode sheet only tags FURNITURE showrooms:
#
#   furniture (default): the client's own per-pincode assignment.
#     exact pincode tag  →  else, for a known/geocoded pincode, nearest TAGGED
#     pincode, inheriting its tag (keeps the client's territory rules in charge
#     even for gaps without accepting made-up pincodes).
#
#   doors / fhc: no client tagging exists, so nearest STORE by distance over the
#     handful of doors/FHC showrooms (curated city coords).
#
# Both return a canonical store label; the (future) template layer maps that to
# the store-address template to send. None → caller falls back to city / asks.

import json
import math
import re
from pathlib import Path

_DATA = Path(__file__).parent / "data"

# Verticals that use the furniture pincode tags. Anything not doors/fhc (incl.
# an unknown/blank vertical) defaults to furniture — the largest network.
_DOORS_FHC = {"doors", "fhc"}

_geo: dict | None = None          # {pincode: [lat, lon]} — offline geocoder
_pin_tag: dict | None = None      # {pincode: showroom_tag}
_tagged_pts: list | None = None   # [(lat, lon, tag)] for tagged AND geocoded pins
_nf_stores: list | None = None    # [{vertical, store, city, lat, lon}]


def _load() -> None:
    global _geo, _pin_tag, _tagged_pts, _nf_stores
    if _geo is not None:
        return
    _geo = json.loads((_DATA / "pincode_geo.json").read_text())
    t = json.loads((_DATA / "pincode_tags.json").read_text())
    tags = t["tags"]
    _pin_tag = {p: tags[i] for p, i in t["pins"].items()}
    # Precompute the fallback search set once: tagged pincodes we can also place.
    _tagged_pts = [(_geo[p][0], _geo[p][1], tag)
                   for p, tag in _pin_tag.items() if p in _geo]
    _nf_stores = json.loads((_DATA / "nonfurniture_stores.json").read_text())


def _locate(pin: str):
    """Exact (lat, lon) for a known pincode, else None.

    Do not approximate an unknown value from its first three digits: that made
    invalid values such as 400963 look like Mumbai and routed them to Worli.
    """
    c = _geo.get(pin)
    if c:
        return c[0], c[1]
    return None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def normalize_pincode(value) -> str | None:
    """A clean 6-digit Indian pincode (first digit 1-9) or None."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 6 and digits[0] != "0" else None


def extract_pincode(text: str) -> str | None:
    """Best-effort pincode from a free-text DM. A standalone 6-digit run (first
    digit 1-9) that is NOT part of a longer number — so a 10-digit phone or an
    order id is never mistaken for a pincode. Returns the first match or None."""
    for m in re.finditer(r"(?<!\d)([1-9]\d{5})(?!\d)", str(text or "")):
        return m.group(1)
    return None


def is_known_pincode(value) -> bool:
    """Whether a pincode exists in an authoritative local source.

    The geocoder is the validity source for otherwise-unmapped pincodes. The
    client's exact dealer mapping is also trusted, so a newly assigned pincode
    can route even if the geocoder snapshot has not caught up yet.
    """
    _load()
    pin = normalize_pincode(value)
    return bool(pin and (pin in _geo or pin in _pin_tag))


def resolve(pincode, vertical: str = "furniture") -> dict | None:
    """Resolve a pincode + product vertical to a showroom.

    Returns {store, vertical, mode, distance_km?} or None when the pincode can't
    be placed (caller then falls back to the city they gave, or asks for one).
      mode == "exact"           → the client's tag for this exact pincode
      mode == "nearest_pincode" → furniture: nearest tagged pincode's showroom
      mode == "nearest_store"   → doors/fhc: nearest store by distance
    """
    _load()
    pin = normalize_pincode(pincode)
    if not pin or not is_known_pincode(pin):
        return None
    vert = (vertical or "").strip().lower()

    if vert in _DOORS_FHC:
        loc = _locate(pin)
        if loc is None:
            return None
        cands = [s for s in _nf_stores if s["vertical"] == vert]
        if not cands:
            return None
        best = min(cands, key=lambda s: _haversine(loc[0], loc[1], s["lat"], s["lon"]))
        return {"store": best["store"], "city": best["city"], "vertical": vert,
                "mode": "nearest_store",
                "distance_km": round(_haversine(loc[0], loc[1], best["lat"], best["lon"]), 1)}

    # furniture (and any unknown vertical): the client's pincode tags.
    tag = _pin_tag.get(pin)
    if tag is not None:
        return {"store": tag, "vertical": "furniture", "mode": "exact"}
    loc = _locate(pin)
    if loc is None:
        return None
    blat, blon, btag = min(
        _tagged_pts, key=lambda p: _haversine(loc[0], loc[1], p[0], p[1]))
    return {"store": btag, "vertical": "furniture", "mode": "nearest_pincode",
            "distance_km": round(_haversine(loc[0], loc[1], blat, blon), 1)}
