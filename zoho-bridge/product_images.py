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


# Customer-vocabulary equivalences for variant hints ("3-seater" vs "Three
# Seater", grey vs gray). Seater-count words matter most in practice.
_EQ = {"grey": ("grey", "gray"), "gray": ("gray", "grey"),
       "1": ("1", "one"), "one": ("one", "1"),
       "2": ("2", "two"), "two": ("two", "2"),
       "3": ("3", "three"), "three": ("three", "3")}


def _match_score(name: str, toks: list[str]) -> int:
    text = (name or "").lower()
    return sum(any(a in text for a in _EQ.get(t, (t,))) for t in toks)


def _caption(v: dict, family: str) -> str:
    """Customer-facing caption: the variant name, unless the sitemap gave us
    junk (bare digits like '2') — then the family name."""
    name = (v.get("variant") or "").strip()
    return name if re.search(r"[a-z]", name, re.I) else family.title()


def share_set(family: str, prefer: str | None = None, compare: bool = False,
              exclude=None) -> tuple[list[tuple[str, str]], str | None]:
    """The customer-facing photo set. Every variant's FIRST image is the
    site's front view — that is always the one we lead with.
    Default: one front-view photo per variant (max 3, site order); a
    single-variant family → up to two photos of it.
    `prefer`: customer's colour/size words — that variant ranks first (and
    the listing link points at it). `compare`: exactly one front view.
    `exclude`: image URLs already sent this conversation — never repeated;
    with `prefer` this becomes a targeted top-up of just that variant.
    Returns ([(caption, image_url)…], listing_link)."""
    exclude = set(exclude or ())
    vs = variants(family, limit=8)
    if not vs:
        return [], None
    if prefer:
        toks = [t for t in re.split(r"[^a-z0-9]+", prefer.lower()) if t]
        vs = sorted(vs, key=lambda v: -_match_score(v.get("variant", ""), toks))

    def fresh(v):
        return [i for i in v.get("images", []) if i not in exclude]

    link_url = vs[0].get("url")
    if compare:
        for v in vs:
            imgs = fresh(v)
            if imgs:
                return [(_caption(v, family), imgs[0])], link_url
        return [], link_url
    if prefer and exclude:      # targeted top-up: only the asked variant
        imgs = fresh(vs[0])[:2]
        return [(_caption(vs[0], family), i) for i in imgs], link_url
    if len(vs) == 1:
        v = vs[0]
        return [(_caption(v, family), img) for img in fresh(v)[:2]], link_url
    out = []
    for v in vs[:3]:
        imgs = fresh(v)
        if imgs:
            out.append((_caption(v, family), imgs[0]))
    return out, link_url


def link(family: str) -> str | None:
    vs = variants(family, limit=1)
    return vs[0].get("url") if vs else None
