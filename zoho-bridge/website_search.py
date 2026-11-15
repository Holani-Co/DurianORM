# Live durian.in product search via Unbxd — the storefront's own search
# service, so the agent sees exactly what a customer browsing the site sees:
# current, sellable products with live prices, real listing links and the
# site's own taxonomy ("l shaped sofa" → Sectional Sofas, never a corner
# table). This replaced price-sheet search as the customer-facing product
# source: the monthly sheet also carries items the site no longer sells,
# which used to surface dead families with no link and wrong-category rows.
# The keys are the site's PUBLIC client-side search credentials (shipped to
# every browser inside durian.in's frontend JS); env-overridable in case the
# site rotates them. The price sheet remains the source for EMI/SKU internals.

import re
from urllib.parse import urlsplit

import httpx

import config

# Trim the payload to what we serve — the full feed is ~60 fields/row.
_FIELDS = ",".join((
    "title", "category", "mrp", "sellingPrice", "productURL", "imageURL",
    "availability", "onlineStock", "exclusive", "discontinuedProducts",
    "productId", "_root_", "vertical"))

# The same feed also carries rows that are NOT on the public storefront: the
# B2B catalogue, private-label items and Full Home Customisation modules. Their
# productURL is a bare slug glued onto the domain ("https://www.durian.in" +
# "presley21" → "https://www.durian.inpresley21"), there is no page behind it
# (404 even with the "/" restored) and most carry no price. A customer's
# "6 seater dining table" matched only two such private rows (conv 12721); the
# send-guardrail then held every reply quoting them, so the customer got
# nothing. Only rows with a real durian.in page are products we can offer, and
# B2B/private rows stay out even if the site ever gives them a URL.
_NON_RETAIL_VERTICALS = {"b2b catalogue", "private"}


def _first(v):
    return v[0] if isinstance(v, (list, tuple)) and v else v


def _true(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "y", "yes")


def storefront_link(url: str) -> bool:
    """A real durian.in page: http(s), a durian.in host, and a path."""
    try:
        u = urlsplit(url or "")
        host = (u.hostname or "").rstrip(".")
    except ValueError:
        return False
    return (u.scheme in ("https", "http")
            and (host == "durian.in" or host.endswith(".durian.in"))
            and bool(u.path.strip("/")))


def normalize(body: dict, rows: int) -> list[dict]:
    """Unbxd response → one entry per product (variant rows share a _root_ —
    first wins, storefront relevance order), live storefront items only."""
    out, seen = [], set()
    for p in (body.get("response") or {}).get("products") or []:
        root = str(p.get("_root_") or p.get("productId") or p.get("title"))
        if root in seen:
            continue
        seen.add(root)
        if _true(p.get("discontinuedProducts")) or not _true(p.get("availability")):
            continue
        if str(p.get("vertical") or "").strip().lower() in _NON_RETAIL_VERTICALS:
            continue
        title = (p.get("title") or "").strip()
        url = (p.get("productURL") or "").strip()
        if not title or not storefront_link(url):
            continue
        out.append({
            "title": title,
            "category": str(_first(p.get("category")) or "").strip(),
            "mrp": p.get("mrp"),
            "selling_price": p.get("sellingPrice") or p.get("mrp"),
            "url": url,
            "image": (p.get("imageURL") or "").strip(),
            "in_store_exclusive": (p.get("exclusive") or "") == "In-store Exclusive",
        })
        if len(out) >= rows:
            break
    return out


_SORTS = {"price_desc": "sellingPrice desc", "price_asc": "sellingPrice asc"}


def _params(query: str, rows: int, sort: str = "",
            min_price=None, max_price=None) -> dict:
    """Unbxd request params. The price axis exists because keyword relevance
    ranks cheap/popular first: 'premium centre table' returns the same budget
    rows plus off-category noise, while `sort=sellingPrice desc` on the plain
    noun query surfaces the ₹50k+ tier the customer actually asked for
    (conv 5569). Sort and range filter are Unbxd-native, verified live."""
    p = {"q": query, "rows": max(rows * 4, 16), "fields": _FIELDS}
    if sort in _SORTS:
        p["sort"] = _SORTS[sort]
    # 0 means UNSET, not "free": schema-filling models send min_price=0,
    # max_price=0 for every call, and a real ₹0 cap would filter out the
    # whole catalog.
    lo = min_price if min_price else None
    hi = max_price if max_price else None
    if lo is not None or hi is not None:
        p["filter"] = (f"sellingPrice:[{int(lo) if lo is not None else '*'} "
                       f"TO {int(hi) if hi is not None else '*'}]")
    return p


# Customer words the storefront files differently. durian.in sells dining only
# as SETS ("4/6/8 Seater Dining Sets"): no sellable row is a "dining table", and
# the site's own search redirects "dining table" to /buy-furniture/
# dining-room-sets. So "6 seater dining table" found nothing sellable while
# "6 seater dining set" finds them all (conv 12721). Used only as a retry when
# the customer's words find nothing, so a future standalone-table range still
# wins on its own name.
_STOREFRONT_WORDS = ((re.compile(r"\bdining[\s-]+tables?\b", re.I), "dining set"),)


def storefront_query(query: str) -> str:
    q = query or ""
    for pat, repl in _STOREFRONT_WORDS:
        q = pat.sub(repl, q)
    return q


async def search(query: str, rows: int = 4, sort: str = "",
                 min_price=None, max_price=None) -> list[dict]:
    """Top live products for a customer query. Raises on network/API failure —
    the skill layer turns that into an honest 'search unavailable'."""
    url = (f"{config.UNBXD_SEARCH_URL}/{config.UNBXD_API_KEY}/"
           f"{config.UNBXD_SITE_KEY}/search")
    async with httpx.AsyncClient(timeout=8) as client:
        async def run(q: str) -> list[dict]:
            r = await client.get(url, params=_params(q, rows, sort,
                                                     min_price, max_price))
            r.raise_for_status()
            return normalize(r.json(), rows)

        found = await run(query)
        retry = storefront_query(query)
        if not found and retry != query:
            found = await run(retry)
        return found
