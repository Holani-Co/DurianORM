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

import httpx

import config

# Trim the payload to what we serve — the full feed is ~60 fields/row.
_FIELDS = ",".join((
    "title", "category", "mrp", "sellingPrice", "productURL", "imageURL",
    "availability", "onlineStock", "exclusive", "discontinuedProducts",
    "productId", "_root_"))


def _first(v):
    return v[0] if isinstance(v, (list, tuple)) and v else v


def _true(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "y", "yes")


def normalize(body: dict, rows: int) -> list[dict]:
    """Unbxd response → one entry per product (variant rows share a _root_ —
    first wins, storefront relevance order), live items only."""
    out, seen = [], set()
    for p in (body.get("response") or {}).get("products") or []:
        root = str(p.get("_root_") or p.get("productId") or p.get("title"))
        if root in seen:
            continue
        seen.add(root)
        if _true(p.get("discontinuedProducts")) or not _true(p.get("availability")):
            continue
        title = (p.get("title") or "").strip()
        url = (p.get("productURL") or "").strip()
        if not title or not url:
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


async def search(query: str, rows: int = 4) -> list[dict]:
    """Top live products for a customer query. Raises on network/API failure —
    the skill layer turns that into an honest 'search unavailable'."""
    url = (f"{config.UNBXD_SEARCH_URL}/{config.UNBXD_API_KEY}/"
           f"{config.UNBXD_SITE_KEY}/search")
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(url, params={
            "q": query, "rows": max(rows * 4, 16), "fields": _FIELDS})
        r.raise_for_status()
        return normalize(r.json(), rows)
