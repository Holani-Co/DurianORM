#!/usr/bin/env python3
# Veronica-in-3-rooms quality trial — the LIVE generation pipeline, end to end,
# outside pytest (real Gemini calls cost real money; run deliberately).
#
#   ./venv/bin/python tests/agent/visualizer_trial.py [FAMILY]
#
# Rooms: drop customer-room photos into tests/agent/rooms/ (room1.jpg …).
# Per room: 1 flash call picks the best variant for the room, 1 flash call
# vets that variant's reference photos (front view first — pick the one that
# shows the full product cleanly), 1 flash call decides placement (Vaibhav's
# rule: exactly one same-type piece in the room → replace it), 1 image-model
# call composes. Output: runs/<date>/viz_trial/ + viz_review.html
# (room | chosen reference | composite, with every verdict printed).

import asyncio
import base64
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config                        # noqa: E402
import product_images                # noqa: E402
import social_agent as sa            # noqa: E402
import website_search                # noqa: E402

FAMILY = (sys.argv[1] if len(sys.argv) > 1 else "VERONICA").upper()
ROOMS = sorted(p for p in (Path(__file__).parent / "rooms").glob("*")
               if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
OUT = Path(__file__).parent / "runs" / date.today().isoformat() / "viz_trial"


def _local_part(path: Path) -> dict:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"inline_data": {"mime_type": mime,
                            "data": base64.b64encode(path.read_bytes()).decode()}}


def _json_from(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


async def pick_variant(room_part: dict, variants: list[dict]) -> dict:
    names = [v.get("variant") or FAMILY for v in variants]
    body = await sa._gemini_generate(config.GEMINI_ANALYSIS_MODEL, [
        room_part,
        {"text": "This is a customer's room. Which ONE of these sofa "
                 f"variants suits the room's palette and style best?\n"
                 f"{json.dumps(names)}\n"
                 'Reply STRICT JSON: {"variant": "<exact name from the '
                 'list>", "why": "<one short line>"}'}], timeout=30)
    out = _json_from(sa._gemini_text(body))
    name = out.get("variant") or names[0]
    match = next((v for v in variants if (v.get("variant") or FAMILY) == name),
                 None)
    if match is None:   # model paraphrased — token-overlap fallback
        toks = set(re.findall(r"[a-z]+", str(name).lower()))
        match = max(variants, key=lambda v: len(
            toks & set(re.findall(r"[a-z]+", (v.get("variant") or "").lower()))))
    return {"variant": match, "why": out.get("why") or ""}


async def vet_reference(variant: dict) -> dict:
    # Candidate pool: the sitemap's variant gallery + the storefront's own
    # listing images (Unbxd), variant-specific AND bare-family queries — the
    # sitemap sometimes carries fabric SWATCHES instead of product shots
    # (Veronica does), and the real shots hide in the family-level feed rows.
    imgs = list(variant.get("images", [])[:2])
    for q in (f"{FAMILY} {variant.get('variant') or ''}".strip(), FAMILY):
        try:
            site = await website_search.search(q, rows=4)
            imgs += [s["image"] for s in site if s.get("image")][:4]
        except Exception as e:
            print(f"  (storefront candidates unavailable: {type(e).__name__})")
    imgs = list(dict.fromkeys(imgs))[:7]
    parts = [await sa._fetch_image_part(u) for u in imgs]
    vname = variant.get("variant") or FAMILY
    body = await sa._gemini_generate(config.GEMINI_ANALYSIS_MODEL, parts + [
        {"text": f"These {len(imgs)} images are candidate reference photos "
                 f"for compositing this product into a room photo: {vname}. "
                 "Which ONE best shows the FULL product (a fabric swatch or "
                 "close-up texture is NOT usable) at a clean front or "
                 "three-quarter angle, no occlusions or overlaid text? "
                 "Also judge whether that photo's upholstery colour matches "
                 f"the target variant ({vname}). Reply STRICT JSON: "
                 '{"best_index": <0-based int>, "why": "<one short line>", '
                 '"usable": <true/false — false only if NONE shows the full '
                 'product>, "matches_colour": <true/false>}'}], timeout=45)
    out = _json_from(sa._gemini_text(body))
    idx = out.get("best_index")
    idx = idx if isinstance(idx, int) and 0 <= idx < len(imgs) else 0
    return {"url": imgs[idx], "index": idx, "why": out.get("why") or "",
            "usable": out.get("usable", True),
            "matches_colour": bool(out.get("matches_colour", True))}


async def decide_placement(room_part: dict, product_desc: str) -> str:
    body = await sa._gemini_generate(config.GEMINI_ANALYSIS_MODEL, [
        room_part,
        {"text": "You are placing furniture in this room photo. The product: "
                 f"{product_desc}. Reply STRICT JSON: "
                 '{"same_type_count": <int>, "same_type_item": "<short '
                 'description if exactly one>", "spot": "<best placement '
                 'phrase if none of the same type>"}'}], timeout=30)
    out = _json_from(sa._gemini_text(body))
    if out.get("same_type_count") == 1:
        return f"replace the existing {out.get('same_type_item') or 'piece'} with it"
    return (out.get("spot") or "").strip() or "at the most natural open spot"


async def compose(room_path: Path, ref_url: str, name: str, placement: str,
                  swatch_url: str = "") -> bytes | None:
    mime = "image/png" if room_path.suffix.lower() == ".png" else "image/jpeg"
    prod = await sa._fetch_bytes(ref_url)
    swatch = await sa._fetch_bytes(swatch_url) if swatch_url else None
    return await sa._compose_preview((room_path.read_bytes(), mime), prod,
                                     name, placement, swatch=swatch)


async def main():
    if not config.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY missing in .env")
    if not ROOMS:
        sys.exit(f"no rooms found — drop room photos into "
                 f"{Path(__file__).parent / 'rooms'}/")
    variants = product_images.variants(FAMILY, limit=8)
    if not variants:
        sys.exit(f"no images on file for {FAMILY}")
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for room in ROOMS:
        print(f"── {room.name} ──")
        room_part = _local_part(room)
        pick = await pick_variant(room_part, variants)
        v = pick["variant"]
        vname = v.get("variant") or FAMILY
        print(f"  variant: {vname}  ({pick['why']})")
        ref = await vet_reference(v)
        print(f"  reference: image[{ref['index']}]  ({ref['why']})")
        placement = await decide_placement(room_part, vname)
        print(f"  placement: {placement}")
        if not ref["usable"]:
            print("  SKIPPING compose — no usable reference "
                  f"({ref['why']})")
        swatch_url = ""
        if ref["usable"] and not ref["matches_colour"]:
            swatch_url = (v.get("images") or [""])[0]
            print(f"  colour mismatch — adding fabric swatch as reference")
        composites = []
        engines = ["gpt-image-2"] + (["gemini"] if config.GEMINI_API_KEY
                                     else [])
        for eng in (engines if ref["usable"] else []):
            config.VISUALIZER_ENGINE = eng     # same inputs, engine varies
            comp = await compose(room, ref["url"], vname, placement,
                                 swatch_url)
            tag = re.sub(r"\W+", "", eng if eng != "gemini"
                         else config.GEMINI_IMAGE_MODEL)
            if comp:
                name_png = f"{room.stem}_{FAMILY.lower()}_{tag}.png"
                (OUT / name_png).write_bytes(comp)
                composites.append({"path": name_png, "engine": eng})
                print(f"  composite[{eng}]: {OUT / name_png}")
            else:
                print(f"  composite[{eng}]: FAILED")
        rows.append({"room": room, "variant": vname, "why": pick["why"],
                     "ref_url": ref["url"], "ref_why": ref["why"],
                     "placement": placement, "composites": composites})

    e = html.escape
    cards = []
    for r in rows:
        room_rel = f"../../rooms/{r['room'].name}"
        figs = [f'<figure><img src="{e(room_rel)}">'
                f'<figcaption>customer room</figcaption></figure>',
                f'<figure><img src="{e(r["ref_url"])}">'
                f'<figcaption>chosen reference</figcaption></figure>']
        for c in r["composites"]:
            figs.append(f'<figure><img src="{e(c["path"])}">'
                        f'<figcaption>composite · {e(c["engine"])}'
                        f'</figcaption></figure>')
        if not r["composites"]:
            figs.append("<figure><p class='fail'>no composite</p></figure>")
        cards.append(f"""
<div class="card">
  <h2>{e(r['room'].name)} → {e(r['variant'])}</h2>
  <p class="meta">variant pick: {e(r['why'])} · reference: {e(r['ref_why'])}
     · placement: {e(r['placement'])}</p>
  <div class="tri" style="grid-template-columns:repeat({len(figs)}, 1fr);">
    {''.join(figs)}
  </div>
</div>""")
    (OUT / "viz_review.html").write_text(f"""<!doctype html>
<meta charset="utf-8"><title>{e(FAMILY)} room trial</title>
<style>
body {{ background:#0d1117; color:#e6edf3; font:14px/1.5 -apple-system,sans-serif;
       max-width:1100px; margin:24px auto; padding:0 16px; }}
.card {{ background:#161b22; border:1px solid #30363d; border-radius:12px;
         padding:16px 20px; margin:18px 0; }}
.meta {{ color:#8b949e; font-size:12px; }}
.tri {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; }}
figure {{ margin:0; }} figcaption {{ color:#8b949e; font-size:11px;
         text-align:center; margin-top:4px; }}
img {{ width:100%; border-radius:8px; }} .fail {{ color:#f85149; }}
</style>
<h1>{e(FAMILY)} — room preview trial</h1>
{''.join(cards)}""", encoding="utf-8")
    print(f"\nreview: {OUT / 'viz_review.html'}")

asyncio.run(main())
