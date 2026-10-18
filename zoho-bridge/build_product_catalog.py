#!/usr/bin/env python3
# OFFLINE build tool (dev-only) — rebuilds the committed FLOOR catalog
# (data/product_catalog.json) from the client's monthly price workbook. Day to
# day the client updates prices in Settings → Product Catalog instead (Google
# Sheet or upload, versioned); this floor is only the fallback when nothing has
# been published there.
#
#   python -m pip install openpyxl
#   python build_product_catalog.py "REVISED SALE PRICE ... .xlsx" [price_period]
#
# Reads two tabs (verified schema, header on row 2, data from row 3):
#   'Durian' : col1 SKU ID, col2 MRP, col3 BP, col4 Offer price (SALE),
#              col9 CATEGORY
#   'RT'     : col1 No.(SKU), col2 Description (name), col4 MRP,
#              col8 BP Incl GST (REVISED SALE price), col10 Category
#
# The customer-facing SALE price (= MRP*(1-disc)) is Durian.Offer price; where a
# SKU is only in RT we fall back to RT's revised sale price (col8). Both are the
# same quantity — the build cross-checks and reports any mismatch.
#
# Output: data/product_catalog.json
#   { "price_period": "...", "products": {
#       "RIHANNA/A/2": {"name","family","mrp","sale_price","category","source"} } }

import json
import sys
from pathlib import Path

import openpyxl

import catalog_import

_OUT = Path(__file__).parent / "data" / "product_catalog.json"


def build(xlsx: str, price_period: str = "") -> None:
    # The merge itself lives in catalog_import (shared with the Settings →
    # Product Catalog upload), so the offline build and the UI never diverge.
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    res = catalog_import.parse_monthly_workbook(wb)
    products = res["products"]
    _OUT.parent.mkdir(exist_ok=True)
    _OUT.write_text(json.dumps({"price_period": price_period, "products": products},
                               separators=(",", ":")))
    named = sum(1 for p in products.values() if p["name"])
    from_rt = sum(1 for p in products.values() if p["source"] == "rt")
    print(f"product_catalog.json: {len(products)} products "
          f"({named} with a name, {from_rt} priced from RT fallback)")
    print(f"  cross-check: {res['stats']['mismatches']} SKUs where Durian.Offer vs "
          f"RT.revised differ >1%")
    print(f"  Durian rows: {res['stats']['durian_rows']} | RT rows: {res['stats']['rt_rows']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
