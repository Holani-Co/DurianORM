# Product catalog resolver — turns what a customer says ("the Rihanna sofa",
# "Meagan 3 seater") into a Durian SKU + sale price, for the EMI / product flows.
# Pure stdlib; reads data/product_catalog.json (built by build_product_catalog.py
# from the client's monthly price sheet — regenerate when prices change).
#
# The customer-facing product name is the SKU FAMILY (RIHANNA, MEAGAN), i.e. the
# part before the first "/", NOT the generic description ("COFFEE TABLE"). So we
# match primarily on the family, then rank within it by description overlap
# ("3 seater", "recliner"). Many families have several variants at different
# prices, so search() returns candidates and the caller disambiguates.

import difflib
import json
import re
from pathlib import Path

_PATH = Path(__file__).parent / "data" / "product_catalog.json"
_data: dict | None = None          # {"price_period", "products": {sku: {...}}}
_by_family: dict | None = None      # {family_lower: [sku, ...]}


def _load() -> dict:
    global _data, _by_family
    if _data is None:
        _data = json.loads(_PATH.read_text(encoding="utf-8"))
        _by_family = {}
        for sku, p in _data.get("products", {}).items():
            _by_family.setdefault((p.get("family") or "").lower(), []).append(sku)
    return _data


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).strip()


def price_period() -> str:
    return _load().get("price_period", "")


def get(sku: str) -> dict | None:
    """The catalog entry for an exact SKU (e.g. 'RIHANNA/A/2'), or None."""
    p = _load().get("products", {}).get(str(sku or "").strip().upper())
    return {"sku": str(sku).strip().upper(), **p} if p else None


def search(query: str, limit: int = 6) -> list[dict]:
    """Products matching a free-text product name, best first. Matches on the SKU
    family (exact / fuzzy) and ranks by description-token overlap. Returns
    [{sku, name, family, sale_price, mrp, category}, ...]."""
    _load()
    q = _norm(query)
    if not q:
        return []
    qtokens = set(q.split())
    families = list(_by_family)

    fam_hits: set[str] = set()
    for tok in qtokens:
        if len(tok) < 3:
            continue
        if tok in _by_family:
            fam_hits.add(tok)
        else:
            fam_hits.update(difflib.get_close_matches(tok, families, n=3, cutoff=0.84))

    scored: list[tuple[int, str]] = []
    if fam_hits:
        for fam in fam_hits:
            for sku in _by_family[fam]:
                desc = set(_norm(_data["products"][sku].get("name")).split())
                scored.append((100 + len(qtokens & desc), sku))
    else:
        # no family hit → fall back to description-token match ("recliner", "sofa")
        for sku, p in _data["products"].items():
            overlap = len(qtokens & set(_norm(p.get("name")).split()))
            if overlap:
                scored.append((overlap, sku))

    scored.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for _, sku in scored:
        if sku in seen:
            continue
        seen.add(sku)
        out.append({"sku": sku, **_data["products"][sku]})
        if len(out) >= limit:
            break
    return out


def family_variants(query: str, limit: int = 8) -> list[dict]:
    """Distinct-by-description variants of the ONE family the query names, best
    first — or [] when the query doesn't cleanly name a single family. Lets a bare
    'meagan' enumerate 1str/2str/3str/tables instead of just the top-ranked colour
    variant that search() returns. Used by the availability flow for a truthful
    'here's the range' answer."""
    _load()
    toks = [t for t in _norm(query).split() if len(t) >= 3]
    fams: set[str] = set()
    for t in toks:
        if t in _by_family:
            fams.add(t)
        else:
            fams.update(difflib.get_close_matches(t, list(_by_family), n=1, cutoff=0.84))
    if len(fams) != 1:
        return []                       # ambiguous across families, or none → caller falls back
    fam = fams.pop()
    out, seen = [], set()
    for sku in _by_family[fam]:
        p = _data["products"][sku]
        k = (p.get("name") or "").strip().lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({"sku": sku, **p})
        if len(out) >= limit:
            break
    return out


def resolve(query: str) -> dict | None:
    """A single unambiguous product for the query, or None when it's ambiguous
    (several variants) or no match — the caller then shows candidates / asks."""
    hits = search(query, limit=8)
    return hits[0] if len(hits) == 1 else None
