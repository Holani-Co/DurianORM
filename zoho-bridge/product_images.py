# Product images + listing links from data/product_images.json (built by
# build_product_images.py from durian.in's image sitemap). Lookup is
# family-first with a descriptor-aware fallback: the site files "LEWIS CORNER"
# under plain "lewis … sectional", so a miss retries the first-token bucket
# ranked by the remaining family tokens (corner ≈ sectional).

import json
import re
from pathlib import Path

_PATH = Path(__file__).parent / "data" / "product_images.json"
_data: dict | None = None

_ALTS = {"corner": ("corner", "sectional", "l shape")}


def _load() -> dict:
    global _data
    if _data is None:
        _data = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    return _data


def variants(family: str, limit: int = 3) -> list[dict]:
    """[{variant, url, images[]}] for a catalog family, best matches first."""
    d = _load()
    fam = re.sub(r"\s+", " ", str(family or "").strip().upper())
    hits = list(d.get(fam) or [])
    if not hits:
        # Sibling extension: "BENJAMIN CORNER" → the site's "BENJAMIN CORNER-I".
        for key in sorted(d, key=len):
            if key.startswith(fam) and d[key]:
                hits = list(d[key])
                break
    if not hits and " " in fam:
        first, rest = fam.split(" ", 1)
        bucket = d.get(first) or []
        rest_toks = rest.lower().split()

        def score(v):
            text = v.get("variant", "").lower()
            return -sum(any(a in text for a in _ALTS.get(t, (t,)))
                        for t in rest_toks)
        ranked = sorted(bucket, key=score)
        hits = [v for v in ranked if score(v) < 0] or ranked
    out, seen = [], set()
    for v in hits:
        if v.get("variant") in seen or not v.get("images"):
            continue
        seen.add(v.get("variant"))
        out.append(v)
        if len(out) >= limit:
            break
    return out


def share_set(family: str) -> tuple[list[tuple[str, str]], str | None]:
    """The customer-facing photo set per the client's rule: several variants →
    one photo each (max 3); a single variant → up to two photos of it.
    Returns ([(caption, image_url)…], listing_link)."""
    vs = variants(family, limit=3)
    if not vs:
        return [], None
    link_url = vs[0].get("url")
    if len(vs) == 1:
        v = vs[0]
        caption = v.get("variant") or family
        return [(caption, img) for img in v.get("images", [])[:2]], link_url
    return [(v.get("variant") or family, v["images"][0]) for v in vs], link_url


def link(family: str) -> str | None:
    vs = variants(family, limit=1)
    return vs[0].get("url") if vs else None
