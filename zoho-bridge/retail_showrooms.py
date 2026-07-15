# Retail showroom directory + matchers.
#
# Retail product-purchase enquiries (a few pieces of furniture — NOT bulk) route
# by city -> showroom -> CRM owner_id, which an agent tags on the deal via the
# Create Deal button. The data lives in retail_showrooms.yaml (generated from
# the client's Matrix-Enquiry sheet, Q=Retail rows only). This module loads it
# and matches a customer-supplied city / showroom choice against it.
import os
import re

import yaml

_PATH = os.path.join(os.path.dirname(__file__), "retail_showrooms.yaml")


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("cities", {}) or {}
    except Exception as e:
        print(f"[retail] could not load {_PATH}: {e}")
        return {}


CITIES = _load()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# Common city variants → the directory key. Note the NCR cluster: the sheet
# groups Gurgaon / Noida / Faridabad under "Delhi", so we do too.
_ALIASES = {
    "bengaluru": "bangalore", "blr": "bangalore",
    "gurgaon": "delhi", "gurugram": "delhi", "noida": "delhi",
    "faridabad": "delhi", "ncr": "delhi", "newdelhi": "delhi", "gzb": "delhi",
    "ghaziabad": "delhi",
    "bombay": "mumbai", "navimumbai": "mumbai", "thane": "mumbai",
    "calcutta": "kolkata",
    "vizag": "visakhapatnam", "vishakhapatnam": "visakhapatnam",
    "allahabad": "prayagraj",
}


def lookup_city(name: str):
    """Match a customer-supplied city name to a directory (key, data) or None.
    Tries exact key, alias, then a contains-match ('Bengaluru Whitefield')."""
    s = _slug(name)
    if not s:
        return None
    if s in CITIES:
        return s, CITIES[s]
    if s in _ALIASES and _ALIASES[s] in CITIES:
        k = _ALIASES[s]
        return k, CITIES[k]
    # contains-match: the customer's text may carry extra words / a locality
    for k in CITIES:
        if k and (k in s or s in k):
            return k, CITIES[k]
    for alias, k in _ALIASES.items():
        if alias in s and k in CITIES:
            return k, CITIES[k]
    return None


def showrooms(city_data: dict) -> list:
    return city_data.get("showrooms") or []


def list_showrooms_text(city_data: dict) -> str:
    """Numbered, human-friendly showroom list for the follow-up email."""
    lines = []
    for i, s in enumerate(showrooms(city_data), 1):
        lines.append(f"{i}. {s.get('location') or s.get('owner_name') or 'Durian showroom'}")
    return "\n".join(lines)


def match_showroom(city_data: dict, reply: str):
    """Match the customer's reply to ONE showroom in the city. Handles a number
    ('option 2'), a locality/name ('JP Nagar', 'Marathahalli'), or a clear
    substring. Returns the showroom dict, or None if it can't be pinned down."""
    rooms = showrooms(city_data)
    if not rooms:
        return None
    if len(rooms) == 1:
        return rooms[0]
    text = (reply or "").lower().strip()
    # 1) explicit number ("2", "option 2", "the 3rd one")
    m = re.search(r"\b([1-9][0-9]?)\b", text)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(rooms):
            # only trust a bare number when the text is short / option-like
            if len(text) <= 25 or "option" in text or "number" in text:
                return rooms[idx]
    # 2) locality / name token overlap — pick the showroom whose location shares
    #    the most distinctive words with the reply.
    best, best_score = None, 0
    for s in rooms:
        loc = (s.get("location") or "").lower()
        toks = [t for t in re.split(r"[^a-z0-9]+", loc) if len(t) > 2
                and t not in ("durian", "bengaluru", "mumbai", "delhi", "the")]
        score = sum(1 for t in toks if t in text)
        if score > best_score:
            best, best_score = s, score
    return best if best_score > 0 else None
