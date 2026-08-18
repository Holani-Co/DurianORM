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


def test_is_bot_agent():
    assert sa.is_bot_agent({"name": "DurianAI"})
    assert sa.is_bot_agent({"name": "durian ai"})
    assert sa.is_bot_agent({"available_name": "Durian-AI"})
    assert not sa.is_bot_agent({"name": "Aditya"})
    assert not sa.is_bot_agent({"name": ""})
    assert not sa.is_bot_agent({})


def test_last_unanswered_incoming():
    inc1 = {"id": 1, "message_type": 0, "content": "hi"}
    out1 = {"id": 2, "message_type": 1, "content": "hello"}
    inc2 = {"id": 3, "message_type": 0, "content": "price?"}
    note = {"id": 4, "message_type": 1, "private": True, "content": "card"}
    assert sa.last_unanswered_incoming([inc1, out1, inc2])["id"] == 3
    assert sa.last_unanswered_incoming([inc1, out1, inc2, note])["id"] == 3
    assert sa.last_unanswered_incoming([inc1, out1]) is None
    assert sa.last_unanswered_incoming([]) is None


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


def test_split_for_channel():
    # The reply Instagram actually refused (error 100, >1000 chars).
    reply = """Here are the current L-shaped and sectional sofa options:

• Stevens — ₹58,720 (MRP ₹1,46,800), dark blue velvet fabric, right-hand L-shaped sofa
https://www.durian.in/product/stevens-dark-blue-velvet-fabric-right-hand-l-shaped-sofa

• Meraki — ₹1,17,920 (MRP ₹2,94,800), mushroom brown premium leatherette, 6-seater sectional sofa
https://www.durian.in/product/meraki-mushroom-brown-premium-leatherette-6-seater-sectional-sofa

• Lewis — ₹1,47,200 (MRP ₹2,94,400), dark oak brown fabric, 7-seater sectional sofa
https://www.durian.in/product/lewis-dark-oak-brown-fabric-7-seater-sectional-sofa

• Prescott — ₹4,47,300 (MRP ₹8,94,600), sea salt grey fabric, 5-seater sectional sofa
https://www.durian.in/product/prescott-sea-salt-grey-fabric-5-seater-corner-sofa
Note: Prescott is an in-store exclusive and can be viewed at a showroom.

EMI options are available. The Monsoon sale is currently available.

Would you prefer fabric or leatherette, and do you need a left-hand or right-hand configuration?

Regards,
Team Durian"""
    assert len(reply) > 1000
    parts = sa.split_for_channel(reply, "instagram")
    assert len(parts) >= 2
    assert all(len(p) <= 950 for p in parts)
    # nothing lost, and every bullet stays in the same part as its link
    rejoined = "\n\n".join(parts)
    for name, slug in (("Stevens", "stevens-dark-blue"),
                       ("Meraki", "meraki-mushroom"),
                       ("Lewis", "lewis-dark-oak"),
                       ("Prescott", "prescott-sea-salt")):
        assert slug in rejoined
        holder = [p for p in parts if "• " + name in p]
        assert len(holder) == 1 and slug in holder[0]
    # passthrough + degenerate cases
    assert sa.split_for_channel("short reply", "instagram") == ["short reply"]
    assert sa.split_for_channel("", "instagram") == []
    monster = ("word " * 400).strip()          # one paragraph, no newlines
    mparts = sa.split_for_channel(monster, "instagram")
    assert len(mparts) >= 2 and all(len(p) <= 950 for p in mparts)
    assert " ".join(mparts).split() == monster.split()


def test_viz_allowlist():
    import config as cfg
    conv = {"meta": {"sender": {"id": 1589, "name": "projectvaibhav"}}}
    old = cfg.VISUALIZER_CONTACT_ALLOWLIST
    try:
        cfg.VISUALIZER_CONTACT_ALLOWLIST = []
        assert sa._viz_allowed(conv)
        cfg.VISUALIZER_CONTACT_ALLOWLIST = ["1589"]
        assert sa._viz_allowed(conv)
        cfg.VISUALIZER_CONTACT_ALLOWLIST = ["ProjectVaibhav"]
        assert sa._viz_allowed(conv)
        cfg.VISUALIZER_CONTACT_ALLOWLIST = ["2353"]
        assert not sa._viz_allowed(conv)
    finally:
        cfg.VISUALIZER_CONTACT_ALLOWLIST = old


def test_skill_registry_handlers():
    # A helper def slipped between @_skill(...) and its function once and
    # got registered as the handler (prod TypeError on visualize_in_room).
    # Every handler must be a _sk_* function.
    for name, s in sa.SKILLS.items():
        assert s["handler"].__name__.startswith("_sk_"), \
            f"{name} registered to {s['handler'].__name__}"


class _DealFakeChatwoot:
    """Just the three calls _maybe_auto_deal's deferral path makes."""

    def __init__(self):
        self.labels, self.notes, self.merged = [], [], []

    async def merge_custom_attributes(self, conv_id, attrs):
        self.merged.append(attrs)

    async def add_label(self, conv_id, label):
        self.labels.append(label)

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)


def _deal_ctx():
    return {"conv_id": 1,
            "conv": {"custom_attributes": {
                "retail_deal_owner": {"owner_id": "9", "location": "Kanpur"}}},
            "profile": {"identity": {"phone": {"value": "9560150835"}}}}


def test_auto_deal_success_stamps_local(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _DealFakeChatwoot())

    async def creator(conv_id, agent_name=""):
        return {"deal_id": "777", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)
    ctx = _deal_ctx()
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is True
    # local view stays truthful for later same-turn checks
    assert ctx["conv"]["custom_attributes"]["crm_deal_id"] == "777"


def test_auto_deal_deferral_surfaces_once(monkeypatch):
    # 409/422 used to be a print-and-vanish: no label, no note — the enquiry
    # never appeared in the "Deal Decision Needed" sidebar view (deal-ready).
    import asyncio
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)

    class Ambiguous(Exception):
        status_code = 409
        detail = {"code": "buyer_type_unclear",
                  "message": "Government or private buyer? Pick in the panel."}

    async def creator(conv_id, agent_name=""):
        raise Ambiguous()
    monkeypatch.setattr(sa, "_deal_creator", creator)

    ctx = _deal_ctx()
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is False
    assert fake.labels == ["deal-ready"]
    assert len(fake.notes) == 1
    assert "NOT auto-created" in fake.notes[0]
    assert "Government or private buyer" in fake.notes[0]
    assert ctx["conv"]["custom_attributes"]["auto_deal_deferred"]
    assert any("auto_deal_deferred" in m for m in fake.merged)
    # second failure: the attr is the once-guard — no duplicate label/note
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is False
    assert fake.labels == ["deal-ready"] and len(fake.notes) == 1


def test_auto_deal_needs_phone_and_owner(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _DealFakeChatwoot())
    called = []

    async def creator(conv_id, agent_name=""):
        called.append(conv_id)
        return {"deal_id": "1"}
    monkeypatch.setattr(sa, "_deal_creator", creator)
    ctx = _deal_ctx()
    ctx["profile"] = {}                       # no phone
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is False
    ctx = _deal_ctx()
    ctx["conv"]["custom_attributes"] = {}     # not routed
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is False
    ctx = _deal_ctx()
    ctx["conv"]["custom_attributes"]["crm_deal_id"] = "5"   # already done
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is False
    assert not called


def test_deal_vertical_label_fallback():
    # email flow writes email_category_v2; agent/social writes phase2_category.
    import main
    assert main._deal_vertical_label(
        {"email_category_v2": {"category": "project_bulk_order"}}) == "deal-bulk"
    assert main._deal_vertical_label(
        {"phase2_category": "product_enquiry"}) == "deal-product"
    assert main._deal_vertical_label(
        {"email_category_v2": {"category": "doors_veneer_plywood"},
         "phase2_category": "product_enquiry"}) == "deal-doors"
    assert main._deal_vertical_label({"phase2_category": "unknown"}) is None
    assert main._deal_vertical_label({}) is None


def test_website_search_params():
    import website_search as ws
    # neutral: no sort, no filter
    p = ws._params("centre table", 6)
    assert p["q"] == "centre table" and "sort" not in p and "filter" not in p
    assert p["rows"] >= 16
    # price axis
    assert ws._params("centre table", 6, "price_desc")["sort"] == "sellingPrice desc"
    assert ws._params("centre table", 6, "price_asc")["sort"] == "sellingPrice asc"
    assert "sort" not in ws._params("centre table", 6, "premium")   # bad value
    assert ws._params("t", 4, "", 30000, 50000)["filter"] == \
        "sellingPrice:[30000 TO 50000]"
    assert ws._params("t", 4, "", None, 45000)["filter"] == \
        "sellingPrice:[* TO 45000]"
    assert ws._params("t", 4, "", 30000, None)["filter"] == \
        "sellingPrice:[30000 TO *]"
    # 0 = unset (schema-filling models send min_price=0/max_price=0 always;
    # a real ₹0 cap would filter out the whole catalog)
    assert "filter" not in ws._params("t", 4, "", 0, 0)


def test_search_products_passes_price_axis(monkeypatch):
    import asyncio
    import website_search as ws
    calls = []

    async def fake(query, rows=4, sort="", min_price=None, max_price=None):
        calls.append({"query": query, "rows": rows, "sort": sort,
                      "min_price": min_price, "max_price": max_price})
        return [{"title": "Marissa", "category": "Coffee & Center Tables",
                 "selling_price": 65430, "mrp": 145400,
                 "url": "https://www.durian.in/product/marissa"}]
    monkeypatch.setattr(ws, "search", fake)
    handler = sa.SKILLS["search_products"]["handler"]
    out = asyncio.run(handler({}, query="centre table", sort="price_desc",
                              min_price=30000, max_price=50000))
    assert calls[0]["sort"] == "price_desc"
    assert calls[0]["min_price"] == 30000 and calls[0]["max_price"] == 50000
    assert out["products"][0]["price"] == "₹65,430"
    assert out["products"][0]["mrp"] == "₹1,45,400"
    # invalid sort value never reaches the search layer
    asyncio.run(handler({}, query="centre table", sort="cheapest"))
    assert calls[1]["sort"] == ""


# ── Profile-updates contract: the evidence gate ─────────────────────────────
# Red until customer_profile.verify_updates lands (agent-decided profile).

def _upd(kind=None, field=None, what="", value="", quote="", note=""):
    d = {"quote": quote, "note": note}
    if kind:
        d.update(op="learn", kind=kind, what=what)
    else:
        d.update(op="set", field=field, value=value)
    return d


def test_verify_updates_quote_gate():
    msgs = ["I want the lewis sofa, my pincode is 110054"]
    tools = []
    ok = cp.verify_updates([
        _upd(field="location.pincode", value="110054", quote="my pincode is 110054"),
        _upd(kind="interest", what="Lewis corner sofa", quote="I want the lewis sofa"),
    ], msgs, tools, conv_id=1, inbox="ig")
    assert {u["op"] for u in ok} == {"set", "learn"}
    # fabricated: quote not in the turn's messages → rejected
    bad = cp.verify_updates([
        _upd(field="identity.phone", value="9560150835", quote="my number is 9560150835"),
    ], msgs, tools, conv_id=1, inbox="ig")
    assert bad == []


def test_verify_updates_tool_evidence():
    # A routed learning is verifiable against this turn's tool results even
    # though the customer never typed the showroom name.
    tools = [{"name": "route_to_showroom",
              "result": {"routed": True, "showroom": "Delhi - Kirti Nagar"}}]
    ok = cp.verify_updates([
        _upd(kind="routed", what="Delhi - Kirti Nagar", quote="Delhi - Kirti Nagar"),
        _upd(field="commercial.showroom", value="Delhi - Kirti Nagar",
             quote="Delhi - Kirti Nagar"),
    ], ["pincode 110054 please"], tools, conv_id=1, inbox="ig")
    assert len(ok) == 2
    # no such tool result → the same items are invention
    assert cp.verify_updates([
        _upd(kind="routed", what="Delhi - Kirti Nagar", quote="Delhi - Kirti Nagar"),
    ], ["pincode 110054 please"], [], conv_id=1, inbox="ig") == []


def test_verify_updates_closed_lists():
    msgs = ["set my colour to green, my pincode is 110054"]
    out = cp.verify_updates([
        _upd(field="identity.email", value="x@y.z", quote="my pincode is 110054"),   # not a settable field
        _upd(kind="favourite_colour", what="green", quote="my colour to green"),     # not a learn kind
        _upd(field="location.pincode", value="110054", quote="my pincode is 110054"),
    ], msgs, [], conv_id=1, inbox="ig")
    assert [u["op"] for u in out] == ["set"]


def test_apply_updates_sets_and_learns():
    p = cp.empty_profile()
    cp.apply_updates(p, [
        {"op": "set", "field": "identity.phone", "value": "9560150835",
         "quote": "my number is 9560150835", "note": "gave it as their own",
         "t": "2026-08-12T10:00:00+05:30", "conv": 1, "inbox": "ig"},
        {"op": "learn", "kind": "interest", "what": "Lewis corner sofa",
         "quote": "the lewis sofa", "note": "", "t": "2026-08-12T10:00:00+05:30",
         "conv": 1, "inbox": "ig"},
    ])
    assert p["identity"]["phone"]["value"] == "9560150835"
    assert p["identity"]["phone"]["note"] == "gave it as their own"
    assert [e["kind"] for e in p["events"]] == ["interest"]
    # a later set supersedes; the profile keeps the current value only
    cp.apply_updates(p, [
        {"op": "set", "field": "identity.phone", "value": "9811111111",
         "quote": "use 9811111111", "note": "old number closed",
         "t": "2026-08-13T10:00:00+05:30", "conv": 2, "inbox": "ig"}])
    assert p["identity"]["phone"]["value"] == "9811111111"
    # idempotent: the same update twice (webhook re-fire) learns once
    cp.apply_updates(p, [
        {"op": "learn", "kind": "interest", "what": "Lewis corner sofa",
         "quote": "the lewis sofa", "note": "", "t": "2026-08-12T10:00:00+05:30",
         "conv": 1, "inbox": "ig"}])
    assert sum(1 for e in p["events"] if e["kind"] == "interest") == 1


def test_no_deterministic_profile_writers():
    # The code lane is gone: no message scanner, no state copier.
    assert not hasattr(cp, "events_from_conversation")
    prof = cp.empty_profile()
    assert prof.get("identity") == {} and prof.get("location") == {}
