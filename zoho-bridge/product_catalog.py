# Product catalog resolver — turns what a customer says ("the Rihanna sofa",
# "Meagan 3 seater") into a Durian SKU + sale price, for the EMI / product flows.
# Source: the price list published in Settings → Product Catalog (config_store
# domain "catalog", from a Google Sheet or an upload). Falls back to the
# committed data/product_catalog.json when nothing valid is published. The
# active version is re-checked at most once a minute, so a publish reaches the
# agent without a restart.
#
# The customer-facing product name is the SKU FAMILY (RIHANNA, MEAGAN), i.e. the
# part before the first "/", NOT the generic description ("COFFEE TABLE"). So we
# match primarily on the family, then rank within it by description overlap
# ("3 seater", "recliner"). Many families have several variants at different
# prices, so search() returns candidates and the caller disambiguates.

import difflib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import config_store

_PATH = Path(__file__).parent / "data" / "product_catalog.json"
_RECHECK_SECONDS = 60
_data: dict | None = None          # {"price_period", "products": {sku: {...}}}
_by_family: dict | None = None      # {family_lower: [sku, ...]}
_meta: dict = {}                    # where _data came from (see meta())
_version_id: int | None = None      # published version evaluated (None = none)
_checked_at = 0.0


def _published() -> tuple[int | None, dict]:
    """(id, metadata) of the active published catalog, or (None, {}). Never
    raises — a store problem means the floor keeps serving."""
    try:
        v = config_store.active_version(config_store.CATALOG)
    except Exception:
        return None, {}
    return (int(v["id"]), v) if v else (None, {})


def _load() -> dict:
    global _data, _by_family, _meta, _version_id, _checked_at
    now = time.monotonic()
    if _data is not None and now - _checked_at < _RECHECK_SECONDS:
        return _data
    _checked_at = now
    vid, ver = _published()
    if _data is not None and vid == _version_id:
        return _data
    _version_id = vid
    doc = config_store.get_active_override(config_store.CATALOG) if vid else {}
    products = doc.get("products")
    if isinstance(products, dict) and products:
        _data = {"price_period": doc.get("price_period", ""), "products": products}
        _meta = {"managed": True, "version_id": vid,
                 "published_at": ver.get("created_at"),
                 "published_by": ver.get("created_by"),
                 "source": doc.get("source") or "upload",
                 "source_name": doc.get("source_name") or "",
                 "format": doc.get("format") or ""}
    else:
        _data = json.loads(_PATH.read_text(encoding="utf-8"))
        _meta = {"managed": False, "source": "built-in"}
    _by_family = {}
    for sku, p in _data.get("products", {}).items():
        _by_family.setdefault((p.get("family") or "").lower(), []).append(sku)
    return _data


def reload() -> None:
    """Forget the cache — the next read re-checks the published version now
    (called right after a publish/rollback so the admin sees it instantly)."""
    global _data, _checked_at
    _data, _checked_at = None, 0.0


def products() -> dict:
    """Every product in the live list, {sku: {...}}."""
    return _load().get("products", {})


def meta() -> dict:
    """Where the live list came from: managed (published in the UI) or the
    built-in floor, plus version/publisher/time, price period and size."""
    data = _load()
    return {**_meta, "price_period": data.get("price_period", ""),
            "count": len(data.get("products", {}))}


def is_fresh(max_age_days: int) -> bool:
    """A list published in the UI within max_age_days — the only list whose
    prices are current enough to quote to a customer. The built-in floor never is."""
    _load()
    if not _meta.get("managed") or not _meta.get("published_at"):
        return False
    try:
        at = datetime.fromisoformat(_meta["published_at"])
    except (TypeError, ValueError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).days < max_age_days


def family_products(family: str) -> list[dict]:
    """All variants of one family, cheapest first."""
    _load()
    rows = [{"sku": sku, **_data["products"][sku]}
            for sku in _by_family.get(str(family or "").lower(), [])]
    return sorted(rows, key=lambda r: r.get("sale_price") or 0)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).strip()


# Customer vocabulary → catalog vocabulary. Durian's sheet says "CORNER SOFA"
# where customers say "L-shaped"; seat counts are "3STR"; TV units are "T V".
_SYNONYMS = [
    (re.compile(r"\bl[\s-]*shaped?\b|\bsectional\b"), "corner"),
    (re.compile(r"\b(\d)[\s-]*seater\b"), r"\g<1>str"),
    (re.compile(r"\btv\b"), "t v"),
]


def _apply_synonyms(q: str) -> str:
    for pat, repl in _SYNONYMS:
        q = pat.sub(repl, q)
    return q


def price_period() -> str:
    return _load().get("price_period", "")


def get(sku: str) -> dict | None:
    """The catalog entry for an exact SKU (e.g. 'RIHANNA/A/2'), or None."""
    p = _load().get("products", {}).get(str(sku or "").strip().upper())
    return {"sku": str(sku).strip().upper(), **p} if p else None


def search(query: str, limit: int = 6, require_family: bool = False) -> list[dict]:
    """Products matching a free-text product name, best first. Matches on the SKU
    family (exact / fuzzy) and ranks by description-token overlap. Returns
    [{sku, name, family, sale_price, mrp, category}, ...].

    require_family=True matches ONLY when the query names a SKU family — for
    callers inferring a product from arbitrary chat text, where the description
    fallback ("sofa", "stand") would hallucinate a product out of small talk."""
    _load()
    q = _apply_synonyms(_norm(query))
    if not q:
        return []
    qtokens = set(q.split())
    families = list(_by_family)

    fam_hits: set[str] = set()
    toks = q.split()
    # Two-word families first ("BENJAMIN CORNER" must beat "BENJAMIN") —
    # adjacent-token bigrams matched exactly against family names.
    for i in range(len(toks) - 1):
        bigram = f"{toks[i]} {toks[i + 1]}"
        if bigram in _by_family:
            fam_hits.add(bigram)
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
    elif not require_family:
        # no family hit → fall back to description-token match ("recliner",
        # "sofa"). Tokens under 3 chars are noise here — "don't" normalises to
        # a stray "t" that would otherwise match "T V STAND" — EXCEPT the
        # synonym-produced "t"/"v" pair, which arrives as adjacent tokens.
        sig = {t for t in qtokens if len(t) >= 3}
        if "t" in qtokens and "v" in qtokens:
            sig |= {"t", "v"}
        for sku, p in _data["products"].items():
            overlap = len(sig & set(_norm(p.get("name")).split()))
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


def _stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok


def family_matches(family: str, query: str) -> bool:
    """Whether a search query is about this family's kind of product — 'recliner'
    or 'sofa' for QUINN (motorized recliners, category RECLINING SOFAS), but not
    'glass table'. Compares the query's nouns with the family's descriptions and
    categories, plurals folded."""
    _load()
    q = {_stem(t) for t in _apply_synonyms(_norm(query)).split() if len(t) >= 3}
    words: set[str] = set()
    for sku in _by_family.get(str(family or "").lower(), []):
        p = _data["products"][sku]
        words |= {_stem(t) for t in _norm(f"{p.get('name')} {p.get('category')}").split()}
    return bool(q & words)


def resolve(query: str) -> dict | None:
    """A single unambiguous product for the query, or None when it's ambiguous
    (several variants) or no match — the caller then shows candidates / asks."""
    hits = search(query, limit=8)
    return hits[0] if len(hits) == 1 else None


def search_families(query: str, limit: int = 4) -> list[dict]:
    """Family-grouped search for presenting DISTINCT products: 40 raw hits
    aggregated to [{family, name, price_from, price_to, variants, sku}] so ten
    fabric codes of one sofa collapse into a single option."""
    groups: dict[str, dict] = {}
    for h in search(query, limit=40):
        fam = h.get("family") or h.get("sku")
        g = groups.setdefault(fam, {"family": fam, "name": h.get("name"),
                                    "sku": h.get("sku"), "variants": 0,
                                    "price_from": h.get("sale_price"),
                                    "price_to": h.get("sale_price"),
                                    "category": h.get("category")})
        g["variants"] += 1
        price = h.get("sale_price")
        if isinstance(price, (int, float)):
            if g["price_from"] is None or price < g["price_from"]:
                g["price_from"] = price
            if g["price_to"] is None or price > g["price_to"]:
                g["price_to"] = price
    return list(groups.values())[:limit]
