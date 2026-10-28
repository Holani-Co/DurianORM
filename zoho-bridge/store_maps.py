# Store Google-Maps links from Durian's own Google Business Profile listings.
#
# The registry's map_url came from the client's sheet, which reused one short
# link for every store sharing a building — the FHC studio in Kirti Nagar opened
# the Durian Furniture store next door, FHC Goregaon the furniture/doors store,
# and four FHC studios were bare coordinate pins that Google resolves to the
# nearest business. Every Durian store has its own GBP listing (its own place),
# so the link is read from there instead: each store is matched to the listing
# of the SAME vertical ("Full Home Customisation" / "Experience Centre for
# Doors" / "Durian Furniture") at its pincode, and the card gets that listing's
# Maps link. A map_url set in Settings → Stores still wins.
import asyncio
import json
import os
import re
from urllib.parse import quote

import httpx

import config
import google_reviews as gr

_STATE_PATH = os.environ.get(
    "STORE_MAPS_STATE_PATH", os.path.join(os.path.dirname(__file__), "store_maps_state.json"))
_REFRESH_SECONDS = 24 * 3600
_READ_MASK = "name,title,metadata,storefrontAddress"

_listings: list | None = None      # [{title, vertical, pincode, words, link}]

# Title words that name the brand / vertical, not the place.
_BRAND_WORDS = {"durian", "furniture", "full", "home", "customisation", "customization",
                "experience", "centre", "center", "for", "doors", "door", "studio",
                "store", "showroom", "the", "and"}


def vertical_of(title: str) -> str:
    t = (title or "").lower()
    if "home custom" in t:
        return "fhc"
    if "door" in t:
        return "doors"
    if "project" in t or "office" in t:
        return ""                    # project offices aren't customer stores
    return "furniture" if "durian" in t else ""


def _words(text: str) -> set:
    return {w for w in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
            if w not in _BRAND_WORDS and not (w.isdigit() and len(w) == 6)}


def _listing(loc: dict) -> dict | None:
    md = loc.get("metadata") or {}
    title = loc.get("title") or ""
    link = md.get("mapsUri") or (
        f"https://www.google.com/maps/search/?api=1&query={quote(title)}"
        f"&query_place_id={md['placeId']}" if md.get("placeId") else "")
    vert = vertical_of(title)
    if not (link and vert):
        return None
    addr = loc.get("storefrontAddress") or {}
    return {"title": title, "vertical": vert, "pincode": str(addr.get("postalCode") or "").strip(),
            "words": sorted(_words(f"{title} {addr.get('locality') or ''}")), "link": link}


async def refresh() -> list:
    """Re-read every GBP location of every account. Raises on a Google error
    (the previous listings stay in use)."""
    global _listings
    out = []
    async with httpx.AsyncClient(timeout=30) as client:
        for acct in await gr.list_account_ids():
            page = None
            while True:
                params = {"readMask": _READ_MASK, "pageSize": 100}
                if page:
                    params["pageToken"] = page
                data = await gr._get(client, f"{gr.BUSINESS_INFO}/{acct}/locations", params)
                out += [x for x in map(_listing, data.get("locations") or []) if x]
                page = data.get("nextPageToken")
                if not page:
                    break
    _listings = out
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f)
    os.replace(tmp, _STATE_PATH)
    print(f"[store-maps] {len(out)} GBP store listings")
    return out


def _load() -> list:
    global _listings
    if _listings is None:
        try:
            with open(_STATE_PATH, encoding="utf-8") as f:
                _listings = json.load(f)
        except (OSError, ValueError):
            _listings = []
    return _listings


def link_for(store: dict) -> str:
    """The Maps link of this store's own GBP listing, or "" when none matches
    unambiguously (the registry link is then kept)."""
    if not config.STORE_MAPS_FROM_GBP:
        return ""
    vert = store.get("primary_vertical") or ""
    same = [x for x in _load() if x["vertical"] == vert]
    if not same:
        return ""
    words = _words(" ".join(str(store.get(k) or "") for k in
                            ("name", "card_name", "location", "city", "address")))
    pin = str(store.get("pincode") or "").strip()
    # Same pincode — unless the listing names a different place (the sheet has
    # wrong pincodes: Gurgaon JMD and Noida Sector 10 carry Kirti Nagar's 110015).
    at_pin = [x for x in same if pin and x["pincode"] == pin
              and (not x["words"] or set(x["words"]) & words)]
    pool = at_pin or [x for x in same if set(x["words"]) and set(x["words"]) <= words]
    if len(pool) > 1:            # several at that pincode → the one naming the place
        best = max(len(set(x["words"]) & words) for x in pool)
        pool = [x for x in pool if len(set(x["words"]) & words) == best]
    links = {x["link"] for x in pool}
    return links.pop() if len(links) == 1 else ""


async def run_forever():
    """Refresh at boot and daily. No-op without Google credentials."""
    if not (config.STORE_MAPS_FROM_GBP and config.GOOGLE_REFRESH_TOKEN):
        return
    while True:
        try:
            await refresh()
        except Exception as e:  # noqa: BLE001
            print(f"[store-maps] refresh failed: {e}")
        await asyncio.sleep(_REFRESH_SECONDS)
