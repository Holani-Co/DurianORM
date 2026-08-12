# Guardrail + profile unit tests — pure functions, zero LLM cost.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import customer_profile as cp
import social_agent as sa


def test_mask_stored_phone():
    r = sa.mask_stored_phone("Your number is +91 95601 50835.", "9560150835", False)
    assert "50835" not in r.replace("ending 0835", "") and "ending 0835" in r
    assert sa.mask_stored_phone("call 9560150835", "9560150835", True) == "call 9560150835"
    assert sa.mask_stored_phone("₹204,880 price", "9560150835", False) == "₹204,880 price"


def test_reask_detector():
    prof = {"identity": {"phone": {"value": "9"}},
            "location": {"city": {"value": "Delhi"}}}
    assert sa.reasks_known_details("please share your Full Name and Zip Code", prof)
    assert sa.reasks_known_details("could you let us know which city you are in", prof)
    assert not sa.reasks_known_details("our team will reach out shortly", prof)
    assert not sa.reasks_known_details("please share your Full Name", {"identity": {}, "location": {}})


def test_link_allowlist():
    assert not sa.link_violation("see https://durian.in/stores and https://maps.app.goo.gl/xyz")
    assert sa.link_violation("visit https://evil.example.com/promo")


def test_scrub():
    out = sa.scrub("**Hello** 🛋️ world 👋\n\n\n📞 Contact: 79820", "")
    assert "**" not in out and "🛋" not in out and "👋" not in out
    assert "📞" in out and "\n\n\n" not in out


def test_comment_rules():
    assert sa.comment_violation("The price is ₹1,20,480")
    assert sa.comment_violation("call me on 9560150835")
    assert not sa.comment_violation("Thank you so much. Please DM us.")
    assert sa.is_low_value_comment("🙏") and sa.is_low_value_comment("gm")
    assert not sa.is_low_value_comment("price please")


def test_last_human_reply_at():
    bot = {"message_type": 1, "created_at": 1000,
           "content_attributes": {"source": "ai_auto_reply"}}
    card = {"message_type": 1, "created_at": 2000, "private": True,
            "content_attributes": {"type": "ai_review_suggestion"}}
    human = {"message_type": 1, "created_at": 3000,
             "content": "Let me look into this for you",
             "content_attributes": {}}
    later = {"message_type": 1, "created_at": 9000,
             "content": "Update: the showroom will call you.",
             "content_attributes": {}}
    empty_artifact = {"message_type": 1, "created_at": 4000, "content": "",
                      "content_attributes": {}}
    legacy_str = {"message_type": 1, "created_at": 5000, "content": "Hello!",
                  "content_attributes": '{"source": "ai_auto_reply"}'}
    assert sa.last_human_reply_at([bot, card]) is None
    assert sa.last_human_reply_at([bot, human]) == 3000
    assert sa.last_human_reply_at([later, bot, human]) == 9000  # newest wins
    assert sa.last_human_reply_at([bot, empty_artifact]) is None
    assert sa.last_human_reply_at([bot, legacy_str]) is None


def test_fold_decline_ordering():
    p = cp.empty_profile()
    cp.merge_events(p, [
        {"t": "2026-08-01T10:00:00+05:30", "msg": 1, "conv": 1, "inbox": "ig",
         "kind": "interest", "what": "EMI on Esmeralda"},
        {"t": "2026-08-05T10:00:00+05:30", "msg": 2, "conv": 1, "inbox": "ig",
         "kind": "declined", "what": "EMI", "quote": "no emi"}])
    f = cp.fold(p)
    assert not any("emi" in str(e.get("what", "")).lower() for e in f["interests"])
    assert f["declined"]
    cp.merge_events(p, [
        {"t": "2026-08-10T10:00:00+05:30", "msg": 3, "conv": 1, "inbox": "ig",
         "kind": "interest", "what": "EMI options again"}])
    assert any("emi" in str(e.get("what", "")).lower()
               for e in cp.fold(p)["interests"])   # re-opened by the customer


def test_merge_idempotent():
    p = cp.empty_profile()
    ev = [{"t": "2026-08-01T10:00:00+05:30", "msg": 1, "conv": 1, "inbox": "ig",
           "kind": "phone", "what": "9560150835"}]
    assert cp.merge_events(p, ev) == 1
    assert cp.merge_events(p, ev) == 0
    assert len(p["events"]) == 1


def test_verify_learned_quote_gate():
    ok = cp.verify_learned(
        [{"kind": "declined", "what": "EMI", "quote": "no EMI, full payment"}],
        ["I said no EMI, full payment please"], 1, "ig")
    assert len(ok) == 1
    bad = cp.verify_learned(
        [{"kind": "declined", "what": "EMI", "quote": "I hate EMI forever"}],
        ["actually EMI sounds nice"], 1, "ig")
    assert bad == []


def test_consolidation_due():
    p = cp.empty_profile()
    p["events_since_consolidation"] = cp.CONSOLIDATE_AFTER_EVENTS
    assert cp.consolidation_due(p)


# ── product image selection (front-view rule + variant hints) ───────────────
import product_images as pi


def test_share_set_front_view_default():
    ph, link = pi.share_set("MEAGAN")
    vs = pi.variants("MEAGAN", limit=8)
    assert len(ph) == 3
    # each photo is that variant's FIRST image — the site's front view
    fronts = {v["variant"]: v["images"][0] for v in vs}
    assert all(fronts[cap] == url for cap, url in ph)
    # site (merchandising) order preserved when no preference stated
    assert [c for c, _ in ph] == [v["variant"] for v in vs[:3]]
    assert link == vs[0]["url"]


def test_share_set_single_variant_two_photos():
    ph, _ = pi.share_set("LIRA")
    vs = pi.variants("LIRA", limit=8)
    assert len(vs) == 1 and len(ph) == 2
    assert [u for _, u in ph] == vs[0]["images"][:2]


def test_share_set_variant_hint_leads():
    ph, link = pi.share_set("MEAGAN", prefer="camel brown")
    vs = pi.variants("MEAGAN", limit=8)
    cam = next(v for v in vs if "Camel" in v["variant"])
    assert ph[0][0] == cam["variant"]
    assert ph[0][1] == cam["images"][0]      # still the front view
    assert link == cam["url"]                # link follows the asked variant


def test_share_set_seater_words_match_digits():
    assert "Two Seater" in pi.share_set("VIVIAN", prefer="2 seater")[0][0][0]
    assert "3 Seater" in pi.share_set("MEAGAN", prefer="three seater")[0][0][0]


def test_share_set_compare_is_one_front_view():
    ph, _ = pi.share_set("MEAGAN", compare=True)
    vs = pi.variants("MEAGAN", limit=8)
    assert ph == [(vs[0]["variant"], vs[0]["images"][0])]


def test_share_set_topup_never_repeats():
    first = [u for _, u in pi.share_set("MEAGAN")[0]]
    ph, _ = pi.share_set("MEAGAN", prefer="camel", exclude=first)
    assert ph and all(u not in first for _, u in ph)
    assert all("Camel" in c for c, _ in ph)


def test_share_set_no_photos_family():
    assert pi.share_set("AMANDA") == ([], None)


def test_share_set_junk_variant_caption_falls_back():
    ph, _ = pi.share_set("ESMERALDA")
    assert ph and all(c == "Esmeralda" for c, _ in ph)


def test_website_search_normalize():
    import website_search as ws
    body = {"response": {"products": [
        {"_root_": "1", "title": "Prescott", "category": ["Sectional Sofas"],
         "mrp": 894600, "sellingPrice": 447300, "availability": "true",
         "discontinuedProducts": "false", "exclusive": "In-store Exclusive",
         "productURL": "https://www.durian.in/product/prescott-corner",
         "imageURL": "https://img/p.jpg"},
        {"_root_": "1", "title": "Prescott", "category": ["Sectional Sofas"],
         "mrp": 894600, "sellingPrice": 447300, "availability": "true",
         "discontinuedProducts": "false",
         "productURL": "https://www.durian.in/product/prescott-corner"},
        {"_root_": "2", "title": "Dead", "availability": "true",
         "discontinuedProducts": "true", "mrp": 100, "sellingPrice": 50,
         "productURL": "https://www.durian.in/product/dead"},
        {"_root_": "3", "title": "Ghost", "availability": "false",
         "mrp": 100, "sellingPrice": 50,
         "productURL": "https://www.durian.in/product/ghost"},
        {"_root_": "4", "title": "NoUrl", "availability": "true", "mrp": 5},
        {"_root_": "5", "title": "Lewis", "category": "Sofas",
         "mrp": 294400, "availability": "true",
         "productURL": "https://www.durian.in/product/lewis"},
    ]}}
    out = ws.normalize(body, rows=4)
    assert [p["title"] for p in out] == ["Prescott", "Lewis"]
    p = out[0]
    assert p["category"] == "Sectional Sofas" and p["in_store_exclusive"]
    assert p["selling_price"] == 447300 and p["mrp"] == 894600
    assert out[1]["selling_price"] == 294400          # falls back to mrp
    assert ws.normalize({}, 4) == []
