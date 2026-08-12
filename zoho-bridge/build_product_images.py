#!/usr/bin/env python3
# Build data/product_images.json from durian.in's product-images sitemap —
# the reference-image + listing-link source for the agent's product-photo
# sharing and the room visualizer. One sitemap fetch, no page scraping.
#
#   {"BENJAMIN CORNER-I": [{"variant": "Ash Grey Premium Leatherette 7 Seater
#     Corner Sofa", "url": "https://www.durian.in/product/…",
#     "images": ["https://images.durian.in/…"]}, …], …}
#
# Slug → family matching: a product slug must START with the catalog family's
# normalized tokens; the LONGEST matching family wins (benjamin-corner-i
# belongs to BENJAMIN CORNER-I, not BENJAMIN). Re-run monthly alongside
# build_product_catalog.py (slugs churn as listings change).

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SITEMAP = "https://www.durian.in/sitemaps/product-images-sitemap.xml"
OUT = Path(__file__).parent / "data" / "product_images.json"
CATALOG = Path(__file__).parent / "data" / "product_catalog.json"
MAX_VARIANTS_PER_FAMILY = 6
MAX_IMAGES_PER_VARIANT = 2


def norm_tokens(s: str) -> list[str]:
    s = re.sub(r"(?<=[a-z])(?=[0-9])", " ", s.lower())   # esmeralda2 → esmeralda 2
    return [t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t]


def main() -> None:
    catalog = json.loads(CATALOG.read_text())
    families = {}
    for p in catalog.get("products", {}).values():
        fam = (p.get("family") or "").strip()
        if fam:
            families.setdefault(tuple(norm_tokens(fam)), fam)
    fam_keys = sorted(families, key=len, reverse=True)   # longest match first
    # Fallback: first-token → family, ONLY when that token starts exactly one
    # family (site slugs drop suffixes: "lewis-…-sectional" ↔ "LEWIS CORNER";
    # ambiguous tokens like "benjamin" — BENJAMIN vs BENJAMIN CORNER — stay
    # strict-prefix only).
    first_tok: dict[str, list] = {}
    for key in fam_keys:
        first_tok.setdefault(key[0], []).append(key)
    unique_first = {t: ks[0] for t, ks in first_tok.items() if len(ks) == 1}

    req = urllib.request.Request(SITEMAP, headers={"User-Agent": "Mozilla/5.0"})
    tree = ET.fromstring(urllib.request.urlopen(req, timeout=60).read())
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
          "i": "http://www.google.com/schemas/sitemap-image/1.1"}

    out: dict[str, list] = {}
    matched = unmatched = 0
    for url in tree.findall("s:url", ns):
        loc = (url.findtext("s:loc", "", ns) or "").strip()
        slug = loc.rsplit("/", 1)[-1]
        toks = norm_tokens(slug)
        fam_display, variant_toks = None, []
        for key in fam_keys:
            if tuple(toks[:len(key)]) == key:
                fam_display = families[key]
                variant_toks = toks[len(key):]
                break
        if not fam_display and toks and toks[0] in unique_first:
            key = unique_first[toks[0]]
            fam_display = families[key]
            variant_toks = toks[1:]
        if not fam_display:
            unmatched += 1
            continue
        matched += 1
        images = [(im.findtext("i:loc", "", ns) or "").strip()
                  for im in url.findall("i:image", ns)][:MAX_IMAGES_PER_VARIANT]
        images = [i for i in images if i]
        if not images:
            continue
        variant = " ".join(variant_toks).title() or fam_display.title()
        bucket = out.setdefault(fam_display.upper(), [])
        if len(bucket) < MAX_VARIANTS_PER_FAMILY:
            bucket.append({"variant": variant, "url": loc, "images": images})

    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{OUT}: {len(out)} families, {matched} products matched, "
          f"{unmatched} slugs unmatched")


if __name__ == "__main__":
    main()
