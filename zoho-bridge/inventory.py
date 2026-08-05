# Stock / availability resolver — turns a Durian SKU (or a name the customer
# says) into an availability answer for the social flows. Pure stdlib; reads
# data/inventory.json (built daily by build_inventory.py from the client's
# 'Inventory Tracker' → 'Final Inventory' tab).
#
# Availability is deliberately CONSERVATIVE: a SKU with no inventory row, or a
# snapshot that's too old, returns None ("unknown") so the caller offers to check
# with the team rather than asserting stock we can't stand behind.

import json
from datetime import datetime, timezone
from pathlib import Path

import product_catalog

_PATH = Path(__file__).parent / "data" / "inventory.json"
_data: dict | None = None
# A snapshot older than this is treated as unusable (client refreshes daily).
STALE_AFTER_HOURS = 48


def _load() -> dict:
    global _data
    if _data is None:
        try:
            _data = json.loads(_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _data = {"products": {}, "generated_at": "", "as_of": ""}
    return _data


def generated_at() -> str:
    return _load().get("generated_at", "")


def as_of() -> str:
    return _load().get("as_of", "")


def is_stale(max_age_hours: int = STALE_AFTER_HOURS) -> bool:
    """True when the snapshot is missing or older than max_age_hours — the caller
    should then decline to assert availability."""
    ts = generated_at()
    if not ts:
        return True
    try:
        gen = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - gen).total_seconds() > max_age_hours * 3600


def get(sku: str) -> dict | None:
    """Raw inventory record for an exact SKU, or None if there's no row."""
    p = _load().get("products", {}).get(str(sku or "").strip().upper())
    return {"sku": str(sku).strip().upper(), **p} if p else None


def availability(sku: str, *, respect_staleness: bool = True) -> dict | None:
    """Availability for an exact SKU, or None when it's unknown (no row, or a
    stale snapshot) — the caller should offer to check with the team.
        {sku, status, stock, sellable, lead_days, arrival_date}
    status ∈ 'in_stock' | 'out_of_stock' | 'discontinued'."""
    if respect_staleness and is_stale():
        return None
    rec = get(sku)
    if not rec:
        return None
    if not rec.get("sellable"):
        status = "discontinued"
    elif (rec.get("stock") or 0) > 0:
        status = "in_stock"
    else:
        status = "out_of_stock"
    return {"sku": rec["sku"], "status": status, "stock": rec.get("stock") or 0,
            "sellable": bool(rec.get("sellable")), "lead_days": rec.get("lead_days"),
            "arrival_date": rec.get("arrival_date")}


def for_query(query: str, *, respect_staleness: bool = True) -> dict | None:
    """Resolve what the customer said to a single SKU (via the price catalog) and
    return its availability, or None when the product is ambiguous/unknown or the
    snapshot is stale. Ambiguous families (Rihanna 2str vs 3str) return None so
    the caller disambiguates first, exactly like the EMI flow."""
    product = product_catalog.resolve(query)
    if not product:
        return None
    avail = availability(product["sku"], respect_staleness=respect_staleness)
    if not avail:
        return None
    return {"product": product, **avail}
