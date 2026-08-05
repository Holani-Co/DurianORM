#!/usr/bin/env python3
# OFFLINE build tool (dev-only) — builds the stock/availability snapshot the
# social flows read, from the client's "Inventory Tracker" workbook. The client
# refreshes that sheet daily; run this after each refresh (a cron on the VM will
# do it) so the bridge answers "is X in stock?" from committed JSON with no
# spreadsheet dependency at runtime.
#
#   python -m pip install openpyxl
#   python build_inventory.py "Inventory Tracker.xlsx" [as_of]
#
# Only the 'Final Inventory' tab is authoritative (per the client). Verified
# schema, header on row 1, data from row 2:
#   col0 Parent (family)   col1 SKU        col5 Cont/Disc
#   col9 Final stock (THE available qty)   col14 Arrival date   col15 Days (lead)
# 'Final stock' was validated against the sheet's own 'Live on Website' stock
# (e.g. ADILON/CT = 14 in both).
#
# Output: data/inventory.json
#   { "generated_at","as_of","source", "products": {
#       "ADILON/CT": {"family","stock","cont_disc","sellable","lead_days",
#                     "arrival_date"} } }
# The build cross-checks coverage against product_catalog.json and reports how
# many catalog SKUs have no inventory row (those answer "unknown" at runtime).

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

_OUT = Path(__file__).parent / "data" / "inventory.json"
_CATALOG = Path(__file__).parent / "data" / "product_catalog.json"
_TAB = "Final Inventory"
# Cont/Disc values that mean "we still sell this" (case-insensitive).
_SELLABLE = {"continued", "offer"}


def _sku(v):
    s = str(v or "").strip().upper()
    return s if s and s != "#N/A" else None


def _int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def build(xlsx: str, as_of: str = "") -> None:
    wb = openpyxl.load_workbook(xlsx, data_only=True, read_only=True)
    ws = wb[_TAB]
    rows = ws.iter_rows(values_only=True)
    header = [str(x).strip() if x is not None else "" for x in next(rows)]
    idx = {h: i for i, h in enumerate(header)}
    try:
        c_par, c_sku, c_cd, c_stk, c_arr, c_days = (
            idx["Parent"], idx["SKU"], idx["Cont/Disc"],
            idx["Final stock"], idx["Arrival date"], idx["Days"])
    except KeyError as e:
        raise SystemExit(f"'{_TAB}' is missing expected column {e}; header was {header}")

    products, dups = {}, 0
    for r in rows:
        sku = _sku(r[c_sku]) if len(r) > c_sku else None
        if not sku:
            continue
        cd = str(r[c_cd]).strip() if len(r) > c_cd and r[c_cd] else ""
        stock = _int(r[c_stk]) if len(r) > c_stk else None
        lead = _int(r[c_days]) if len(r) > c_days else None
        arr = r[c_arr] if len(r) > c_arr else None
        arr = arr.date().isoformat() if isinstance(arr, datetime) else None
        rec = {
            "family": (str(r[c_par]).strip() if len(r) > c_par and r[c_par] else sku.split("/")[0]),
            "stock": stock if stock is not None else 0,
            "cont_disc": cd,
            "sellable": cd.lower() in _SELLABLE,
            "lead_days": lead if (lead is not None and lead > 0) else None,
            "arrival_date": arr,
        }
        if sku in products:
            dups += 1
            # keep the row with more stock — a duplicate is a data glitch, not two bins
            if rec["stock"] <= products[sku]["stock"]:
                continue
        products[sku] = rec

    _OUT.parent.mkdir(exist_ok=True)
    _OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "source": f"{Path(xlsx).name} / {_TAB}",
        "products": products,
    }, separators=(",", ":")))

    in_stock = sum(1 for p in products.values() if p["sellable"] and p["stock"] > 0)
    oos = sum(1 for p in products.values() if p["sellable"] and p["stock"] == 0)
    not_sell = sum(1 for p in products.values() if not p["sellable"])
    print(f"inventory.json: {len(products)} SKUs "
          f"({in_stock} in stock, {oos} sellable-but-0, {not_sell} discontinued/expired)"
          + (f"; {dups} duplicate-SKU rows collapsed" if dups else ""))

    # Coverage cross-check against the price catalog (the SKUs customers can name).
    if _CATALOG.exists():
        cat = set(json.loads(_CATALOG.read_text())["products"])
        have = cat & set(products)
        missing = cat - set(products)
        pct = 100 * len(have) // max(1, len(cat))
        print(f"  catalog coverage: {len(have)}/{len(cat)} catalog SKUs have an "
              f"inventory row ({pct}%); {len(missing)} have none → answered 'unknown'")
        if missing:
            print(f"    e.g. no-stock-row: {sorted(missing)[:6]}")
    else:
        print("  (product_catalog.json not found — skipped coverage cross-check)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
