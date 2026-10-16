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
    # A name is never held in the profile, so asking for it is not a re-ask
    # (conv 9725: "share your full name and what you are planning" was held).
    assert not sa.reasks_known_details(
        "Could you please share your full name and what you are planning?", prof)
    assert sa.reasks_known_details("please share your contact number", prof)
    # The store lookup itself asked for a pincode → a location ask is fine,
    # and the name riding along with it must not trip the detector either.
    assert not sa.reasks_known_details(
        "Please share your full name and area pincode", prof, location_ask_ok=True)
    assert sa.reasks_known_details("please share your phone number and pincode", prof,
                                   location_ask_ok=True)
    # We answering is not us asking: "share" here is ours, the pincode is in hand.
    assert not sa.reasks_known_details(
        "We'll then share the nearest showroom for pincode 201304.", prof)
    assert not sa.reasks_known_details("Our team will send the details to your phone.", prof)


def test_ungrounded_facts():
    """The send/hold test: only a price, number or link found nowhere in what
    the turn could quote is a reason to hold."""
    src = ('{"price": "₹59,800", "link": "https://www.durian.in/product/elentra-high-back"} '
           '📞 8657960901/ 18002093242 🗺️ https://maps.app.goo.gl/viuUEuHPAETCHTiq6 '
           'customer: my number is 9560150835, pincode 110021. template: call 1800 209 3242. '
           'price ₹1,09,520')
    for clean in (
            "Could you tell us what you are planning — a modular kitchen or a wardrobe?",
            "Please share your full name, contact number and city so we can register this.",
            "Elentra — ₹59,800 each\nhttps://www.durian.in/product/elentra-high-back",
            "Kirti Nagar studio, 11.00 AM - 8.00 PM\n📞 8657960901/ 18002093242\n"
            "https://maps.app.goo.gl/viuUEuHPAETCHTiq6",
            "Reach us on 1800 209 3242. More at https://www.durian.in/",
            "Noted: pincode 110021, number 9560150835. Lewis is ₹1,09,520 (Rs 109520)."):
        assert sa.ungrounded_facts(clean, src) == [], clean
    assert sa.ungrounded_facts("Elentra is ₹49,800 today.", src) == ["price ₹49,800"]
    assert sa.ungrounded_facts("Call our manager on 98765 43210.", src) == ["number 98765 43210"]
    assert sa.ungrounded_facts("See https://www.durian.in/product/made-up-sofa", src) == \
        ["link https://www.durian.in/product/made-up-sofa"]
    assert len(sa.ungrounded_facts("₹49,800, or EMI from Rs. 3,499; call 98765 43210", src)) == 3
    # Checked arithmetic on quoted prices is grounded; wrong arithmetic is not.
    assert sa.ungrounded_facts("Elentra ₹59,800 vs Lewis ₹1,09,520 — ₹49,720 more.", src) == []
    assert sa.ungrounded_facts("10 Elentra chairs at ₹59,800 each: ₹5,98,000.", src) == []
    assert sa.ungrounded_facts("10 Elentra chairs at ₹59,800 each: ₹5,50,000.", src) == \
        ["price ₹5,50,000"]


def test_hold_reason_needs_a_checkable_problem(monkeypatch):
    """Send vs hold is code's call. The model's opinion of its own reply is not
    even an input — asking for missing details or a clarifying question always
    goes out (convs 9868, 9928 were held for exactly that)."""
    monkeypatch.setattr(sa.config, "SOCIAL_AUTO_SEND_ENABLED", True)
    monkeypatch.setattr(sa.config, "SOCIAL_AUTO_SEND_MIN_CONFIDENCE", 80)
    src = '{"price": "₹59,800", "link": "https://www.durian.in/product/elentra"} 📞 8657960901'
    empty = {"identity": {}, "location": {}}
    held = {"identity": {"phone": {"value": "9560150835"}},
            "location": {"city": {"value": "Delhi"}}}

    def why(reply, prof=empty, **kw):
        return sa.hold_reason(reply, surface=kw.pop("surface", ""), prof=prof, sources=src, **kw)

    # asks and clarifying questions: sent, confidence 92
    assert why("To register your enquiry, please share your contact number.") == ("", 92)
    assert why("Could you tell us what you are planning — a kitchen or a wardrobe?") == ("", 92)
    assert why("Elentra — ₹59,800\nhttps://www.durian.in/product/elentra\n"
               "Please share your full name, contact number and city.") == ("", 92)
    # one invented fact → 72 → held, and the reason names it
    reason, conf = why("Elentra is ₹49,800 today.")
    assert conf == 72 and "price ₹49,800" in reason
    reason, conf = why("₹49,800 — call 98765 43210")
    assert conf == 52 and "number 98765 43210" in reason
    # a write this turn is always confirmed to the customer
    assert why("Your enquiry is registered. Call 98765 43210.", wrote_this_turn=True)[0] == ""
    # the hard holds are unchanged, and outrank a write
    assert why("please share your phone number", prof=held)[0] == \
        "reply re-asks for details we already hold"
    assert why("Could you share your full name?", prof=held) == ("", 92)
    assert "non-allowlisted link" in why("see https://evil.example.com/x", wrote_this_turn=True)[0]
    assert why("It is ₹59,800", surface="comment")[0] == "price in a public comment"
    assert why("Thank you!", over_budget=True)[0].startswith("turn budget exhausted")
    assert "finish step" in why("Hello there", no_finish=True)[0]
    monkeypatch.setattr(sa.config, "SOCIAL_AUTO_SEND_ENABLED", False)
    assert why("Thank you!")[0] == "auto-send is switched off"


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
    # The agent often re-types the result as COMPACT json; spacing must not
    # decide whether a real quote is accepted.
    assert len(cp.verify_updates([
        _upd(kind="routed", what="Delhi - Kirti Nagar",
             quote='{"routed":true,"showroom":"Delhi - Kirti Nagar"}'),
    ], ["pincode 110054 please"], tools, conv_id=1, inbox="ig")) == 1
    assert cp.verify_updates([
        _upd(kind="routed", what="Mumbai - Worli",
             quote='{"routed":true,"showroom":"Mumbai - Worli"}'),
    ], ["pincode 110054 please"], tools, conv_id=1, inbox="ig") == []
    # ...and it abridges: the leading key/value pair exact, the rest paraphrased.
    full = [{"name": "route_to_showroom",
             "result": {"routed": True, "showroom": "Delhi - Kirti Nagar", "city": "Delhi",
                        "deal_created": True}}]
    assert len(cp.verify_updates([
        _upd(field="commercial.showroom", value="Delhi - Kirti Nagar",
             quote='"showroom": "Delhi - Kirti Nagar", "deal_created": true'),
    ], ["pincode 110054 please"], full, conv_id=1, inbox="ig")) == 1
    assert cp.verify_updates([
        _upd(field="commercial.showroom", value="Mumbai - Worli",
             quote='"showroom": "Mumbai - Worli", "deal_created": true'),
    ], ["pincode 110054 please"], full, conv_id=1, inbox="ig") == []


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


# ── Location change after routing (conv 8405: Bikaner → Kolkata) ─────────────
# A different location must never silently re-route or be claimed as routed:
# before a deal we ask the customer to pick ONE (no deal until they do); after
# a deal we leave CRM alone and flag a human.

_BIKANER = {"owner_id": "3608871000560226015", "location": "Bikaner - Rani Baraz",
            "city": "Bikaner"}


def _routed_ctx(**attrs):
    ca = {"retail_deal_owner": dict(_BIKANER), "retail_customer_phone": "9820649178"}
    ca.update(attrs)
    return {"conv_id": 1, "conv": {"custom_attributes": ca},
            "profile": {"identity": {"phone": {"value": "9820649178"}}}}


class _RouteFakeChatwoot(_DealFakeChatwoot):
    """Adds remove_label — the normal routing path also drops a label."""

    async def remove_label(self, conv_id, label):
        pass


def _route_setup(monkeypatch):
    fake = _RouteFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    created = []

    async def creator(conv_id, agent_name=""):
        created.append(conv_id)
        return {"deal_id": "D1", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)
    return fake, created


def test_route_same_city_unchanged(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx()
    r = asyncio.run(sa._sk_route_to_showroom(ctx, city="Bikaner"))
    assert r["routed"] and "already routed" in r["note"]
    assert not created and "pending_showroom_choice" not in ctx["conv"]["custom_attributes"]


def test_route_new_city_asks_to_confirm(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx()
    r = asyncio.run(sa._sk_route_to_showroom(ctx, city="Kolkata"))
    ca = ctx["conv"]["custom_attributes"]
    assert r["routed"] is False and r["needs_confirmation"]
    assert r["new_showroom"] == "Kolkata - Topsia"
    assert "Do NOT say" in r["note"]
    assert ca["retail_deal_owner"]["location"] == "Bikaner - Rani Baraz"   # not re-routed
    assert ca["pending_showroom_choice"] == {"current": "Bikaner - Rani Baraz",
                                             "new": "Kolkata - Topsia"}
    assert not created


def test_route_confirm_without_asking_still_asks(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx()
    r = asyncio.run(sa._sk_route_to_showroom(ctx, city="Kolkata", confirm=True))
    assert r.get("needs_confirmation") and not created


def test_route_confirmed_switch_reroutes_and_creates(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx(pending_showroom_choice={"current": "Bikaner - Rani Baraz",
                                               "new": "Kolkata - Topsia"})
    r = asyncio.run(sa._sk_route_to_showroom(ctx, city="Kolkata", confirm=True))
    ca = ctx["conv"]["custom_attributes"]
    assert r["routed"] and r["showroom"] == "Kolkata - Topsia" and r["deal_created"]
    assert ca["retail_deal_owner"]["location"] == "Kolkata - Topsia"
    assert not ca.get("pending_showroom_choice")
    assert created == [1]


def test_route_confirmed_current_keeps_and_creates(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx(pending_showroom_choice={"current": "Bikaner - Rani Baraz",
                                               "new": "Kolkata - Topsia"})
    r = asyncio.run(sa._sk_route_to_showroom(ctx, city="Bikaner", confirm=True))
    ca = ctx["conv"]["custom_attributes"]
    assert r["routed"] and r["showroom"] == "Bikaner - Rani Baraz" and r["deal_created"]
    assert ca["retail_deal_owner"]["location"] == "Bikaner - Rani Baraz"
    assert not ca.get("pending_showroom_choice") and created == [1]


def test_route_change_after_deal_flags_human(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx(crm_deal_id="D0")
    r = asyncio.run(sa._sk_route_to_showroom(ctx, city="Kolkata"))
    ca = ctx["conv"]["custom_attributes"]
    assert r["deal_created"] and "do NOT say it has been moved" in r["note"]
    assert ca["retail_deal_owner"]["location"] == "Bikaner - Rani Baraz"
    assert "deal-ready" in fake.labels and fake.notes and not created


def test_auto_deal_waits_for_location_choice(monkeypatch):
    import asyncio
    fake, created = _route_setup(monkeypatch)
    ctx = _routed_ctx(pending_showroom_choice={"current": "a", "new": "b"})
    assert asyncio.run(sa._maybe_auto_deal(ctx)) is False and not created


# ── Non-product photo / shared post with no text → stay silent (conv 8457) ───

def _photo(url="https://cdn/x.jpg", content="Shared post", **attrs):
    return {"message_type": 0, "content": content,
            "attachments": [{"data_url": url, "file_type": "image"}],
            "content_attributes": attrs}


def test_silent_photo_burst_detection():
    reply = {"message_type": 1, "content": "Hi!"}
    activity = {"message_type": 2, "content": "DurianAI added agent-needed"}
    assert sa._silent_photo_burst([_photo()]) == ["https://cdn/x.jpg"]
    # activity lines after the photo are skipped; our earlier reply ends the burst
    assert sa._silent_photo_burst([_photo("a"), reply, _photo("b"), activity]) == ["b"]
    # any real text, a Durian post caption, or a reel → not the silent case
    assert sa._silent_photo_burst([_photo(content="price of this?")]) is None
    assert sa._silent_photo_burst([_photo(shared_post_caption="Benjamin sofa")]) is None
    reel = {"message_type": 0, "content": "",
            "attachments": [{"data_url": "u", "file_type": "ig_reel"}]}
    assert sa._silent_photo_burst([reel]) is None
    assert sa._silent_photo_burst([{"message_type": 0, "content": "hello"}]) is None
    assert sa._silent_photo_burst([_photo(str(i)) for i in range(4)]) is None


class _SilentFakeChatwoot:
    def __init__(self, msgs):
        self.msgs, self.notes = msgs, []

    async def get_conversation_messages_raw(self, conv_id):
        return self.msgs

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)


class _ReachedAgent(Exception):
    pass


def _run_locked(monkeypatch, msgs, verdict, latest="Shared post", msg_id=1):
    import asyncio
    fake = _SilentFakeChatwoot(msgs)
    monkeypatch.setattr(sa, "chatwoot", fake)
    monkeypatch.setattr(sa.config, "PRODUCT_VISION_ENABLED", True)
    monkeypatch.setattr(sa.config, "GEMINI_API_KEY", "k")

    async def fake_verdict(url):
        return verdict
    monkeypatch.setattr(sa, "_vision_verdict", fake_verdict)

    async def reached(*a, **k):
        raise _ReachedAgent()
    monkeypatch.setattr(sa.profile_mod, "load", reached)   # first step after the guard
    sa._last_handled_msgid.pop(99, None)
    conv = {"id": 99, "meta": {"sender": {"id": 5, "name": "S"}}, "custom_attributes": {}}
    try:
        res = asyncio.run(sa._handle_locked(conv, 99, "instagram", "", latest, msg_id))
    except _ReachedAgent:
        res = "agent"
    return res, fake


def test_non_product_photo_stays_silent(monkeypatch):
    res, fake = _run_locked(monkeypatch, [_photo()], {"is_product": False})
    assert res == {"handled": "non_product_photo_silent"}
    assert len(fake.notes) == 1 and "no reply sent" in fake.notes[0]
    assert sa._last_handled_msgid.get(99) == 1


def test_product_photo_still_reaches_agent(monkeypatch):
    res, fake = _run_locked(monkeypatch, [_photo()], {"is_product": True})
    assert res == "agent" and not fake.notes


def test_unviewable_photo_still_reaches_agent(monkeypatch):
    res, fake = _run_locked(monkeypatch, [_photo()], None)       # vision failed
    assert res == "agent" and not fake.notes


def test_photo_with_text_reaches_agent(monkeypatch):
    res, _ = _run_locked(monkeypatch, [_photo(content="is this available?")],
                         {"is_product": False}, latest="is this available?")
    assert res == "agent"


def test_vision_verdict_cached_one_call(monkeypatch):
    import asyncio
    calls = []

    async def fake_fetch(url):
        return {"inline": url}

    async def fake_gen(model, parts, timeout=60):
        calls.append(parts)
        return {}
    monkeypatch.setattr(sa, "_fetch_image_part", fake_fetch)
    monkeypatch.setattr(sa, "_gemini_generate", fake_gen)
    monkeypatch.setattr(sa, "_gemini_text", lambda b: '{"is_product": false}')
    sa._VISION_CACHE.clear()
    assert asyncio.run(sa._vision_verdict("u1")) == {"is_product": False}
    assert asyncio.run(sa._vision_verdict("u1")) == {"is_product": False}
    assert len(calls) == 1
    assert "while asking about a product" not in calls[0][1]["text"]


# ── Per-vertical client guidance (Settings → Agent Prompts) ──────────────────

def _guidance_store(monkeypatch, doc):
    import tempfile
    import config_store
    monkeypatch.setattr(config_store, "_DB_PATH", tempfile.mkdtemp() + "/cs.db")
    config_store.init()
    config_store.publish(doc, domain=config_store.AGENT_PROMPTS)


def _dm_prompt(vertical, guidance):
    from datetime import datetime
    return sa._system_prompt("dm", "inbox", vertical, datetime(2026, 1, 1),
                             "PROFILE", "TPL", 1, "", guidance)


def test_agent_guidance_flag_off_is_noop(monkeypatch):
    _guidance_store(monkeypatch, {"furniture": "Lead with the map link."})
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_VERTICAL_PROMPTS_ENABLED", False)
    assert sa._client_guidance("furniture") == ""
    assert _dm_prompt("furniture", sa._client_guidance("furniture")) == _dm_prompt("furniture", "")


def test_agent_guidance_own_vertical_only(monkeypatch):
    _guidance_store(monkeypatch, {"furniture": "F-NOTE", "fhc": "H-NOTE"})
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_VERTICAL_PROMPTS_ENABLED", True)
    dm = _dm_prompt("furniture", sa._client_guidance("furniture"))
    assert "F-NOTE" in dm and "H-NOTE" not in dm
    assert dm.index("CLIENT GUIDANCE") < dm.index("APPROVED TEMPLATES")   # below the rules
    from datetime import datetime
    cm = sa._system_prompt("comment", "inbox", "fhc", datetime(2026, 1, 1), "P", "T", 0,
                           "", sa._client_guidance("fhc"))
    assert "H-NOTE" in cm and "F-NOTE" not in cm
    assert sa._client_guidance("doors") == ""                       # nothing published
    assert "CLIENT GUIDANCE" not in _dm_prompt("doors", "")


def test_agent_prompts_validator():
    import main
    v = main._validate_agent_prompts_doc
    # Ordinary furniture-business wording must pass cleanly (no false alarms).
    ok = v({"furniture": "Mention inventory availability and always send the map link.",
            "fhc": "Our wardrobes are made up of BWP ply."})
    assert ok["ok"] and not ok["warnings"]
    assert not v({"fhc": "x" * (sa.config.SOCIAL_AGENT_VERTICAL_PROMPT_MAX_CHARS + 1)})["ok"]
    assert not v({"doors": 42})["ok"]
    assert not v({"doors": "hidden\x7ftext"})["ok"]                  # DEL
    assert not v({"doors": "hidden‮text"})["ok"]                # bidi override
    assert v({"doors": "line one\nline two\ttabbed"})["ok"]          # newline/tab fine
    unknown = v({"garden": "hi"})
    assert unknown["ok"] and unknown["warnings"]                     # warns, doesn't block


# ── Doors/FHC lead capture where there's no store (conv 9492, Varanasi) ──────

def test_fhc_prompt_registers_enquiry_not_route():
    from datetime import datetime
    p = sa._system_prompt("dm", "durian_fullhomecustomisation", "fhc", datetime(2026, 1, 1),
                          "PROFILE", "TPL", 1, "", "")
    assert "register_enquiry" in p and "route_to_showroom" not in p   # one enquiry skill
    assert "the account does not decide the line" in p


def _varanasi_upcoming(monkeypatch):
    """No real store anywhere; an FHC (and furniture) store opening in Varanasi."""
    monkeypatch.setattr(sa.config, "STORE_LOCATOR_ENABLED", True)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(sa.store_locator, "upcoming",
                        lambda vert, pincode=None, city=None:
                        {"area": "Varanasi"} if vert in ("fhc", "furniture") and city == "Varanasi" else None)


def test_no_store_notes_point_to_the_right_next_step(monkeypatch):
    _varanasi_upcoming(monkeypatch)
    find = sa.SKILLS["find_showrooms"]["handler"]
    for vert in ("fhc", "furniture"):                      # opening soon → a note, no deal
        soon = find({}, line=vert, city="Varanasi")
        assert soon["upcoming"] and "note_upcoming_store_lead (city: Varanasi)" in soon["note"]
        assert "register_enquiry" not in soon["note"] and "not a yes/no question" in soon["note"]
    none = find({}, line="doors", city="Patna")           # no store at all → register
    assert none["serviceable"] is False and "register_enquiry (line 'doors'" in none["note"]
    assert "register_enquiry" not in find({}, line="furniture", city="Patna")["note"]
    # no line → no store is named: the agent must find out what they want first
    assert find({"vertical": "doors"}, city="Patna")["need_line"] is True


def test_upcoming_store_lead_is_a_note_not_a_deal(monkeypatch):
    import asyncio
    _varanasi_upcoming(monkeypatch)
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)
    note = sa.SKILLS["note_upcoming_store_lead"]["handler"]
    ctx = {"vertical": "fhc", "conv_id": 1, "conv": {"custom_attributes": {}}, "profile": {},
           "incoming_all": ["Sid, 9876543210"]}
    assert asyncio.run(note(ctx, city="Patna", phone="9876543210"))["noted"] is False
    assert asyncio.run(note(dict(ctx, incoming_all=["Yes sure"]), city="Varanasi",
                            phone="9876543210"))["need_phone"]       # not typed → not trusted
    assert not fake.notes
    r = asyncio.run(note(ctx, city="Varanasi", phone="9876543210", name="Sid"))
    assert r["noted"] and len(fake.notes) == 1 and fake.labels == ["upcoming-store-lead"]
    assert "Varanasi" in fake.notes[0] and "9876543210" in fake.notes[0] and "Sid" in fake.notes[0]
    assert all("phase2_category" not in m and "deal_customer_details" not in m for m in fake.merged)
    reg = asyncio.run(sa._sk_register_enquiry(
        ctx, category="fhc", city="Varanasi", phone="9876543210"))
    assert reg["registered"] is False and reg["upcoming"] and len(fake.notes) == 1
    # The store opens (added as a real store — even if the upcoming entry lingers):
    # the normal enquiry/deal flow takes over.
    monkeypatch.setattr(sa.store_locator, "resolve", lambda vert, pincode=None, city=None:
                        {"card_name": "FHC - Varanasi"} if city == "Varanasi" else None)
    assert asyncio.run(note(ctx, city="Varanasi", phone="9876543210"))["noted"] is False
    # Varanasi wasn't typed this chat, so registering needs the agent to confirm it.
    assert asyncio.run(sa._sk_register_enquiry(
        ctx, category="fhc", city="Varanasi", phone="9876543210", confirm=True))["registered"]
    assert "deal-ready" in fake.labels


def test_register_enquiry_without_phone_asks_for_it(monkeypatch):
    import asyncio
    writes = []

    async def merge(*a, **k):
        writes.append(a)
    monkeypatch.setattr(sa.chatwoot, "merge_custom_attributes", merge)
    ctx = {"conv_id": 1, "conv": {"custom_attributes": {}}, "profile": {}, "incoming_all": ["Yes sure"]}
    r = asyncio.run(sa._sk_register_enquiry(ctx, category="fhc", city="Varanasi"))
    assert r["registered"] is False and r["need_phone"] and not writes


# ── FHC WhatsApp → agent (PR: feat/whatsapp-fhc-agent) ───────────────────────

def _wa_conv(inbox_id=25, name="Durian FHC WhatsApp", phone="+919876543210"):
    return {"inbox_id": inbox_id, "inbox": {"name": name},
            "meta": {"sender": {"id": "s1", "name": "Asha", "phone_number": phone}},
            "custom_attributes": {}}


def test_eligible_whatsapp_only_on_designated_inbox(monkeypatch):
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_ENABLED", True)
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_CONTACT_ALLOWLIST", [])
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_WHATSAPP_INBOXES", {"25": "fhc"})
    assert sa.eligible(_wa_conv(25), "whatsapp") is True
    assert sa.eligible(_wa_conv(99), "whatsapp") is False          # other WhatsApp inbox
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_WHATSAPP_INBOXES", {})
    assert sa.eligible(_wa_conv(25), "whatsapp") is False          # map empty → off
    # Instagram path is unchanged by the WhatsApp gate
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_CHANNELS", ("instagram",))
    assert sa.eligible({"meta": {"sender": {}}}, "instagram") is True


def test_whatsapp_agent_vertical_by_id_and_name(monkeypatch):
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_WHATSAPP_INBOXES", {"25": "fhc"})
    assert sa._whatsapp_agent_vertical(_wa_conv(25)) == "fhc"
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_WHATSAPP_INBOXES", {"durian fhc whatsapp": "fhc"})
    assert sa._whatsapp_agent_vertical(_wa_conv(99, name="Durian FHC WhatsApp")) == "fhc"
    assert sa._whatsapp_agent_vertical(_wa_conv(99, name="Other")) is None


def test_seed_whatsapp_phone():
    prof = {"identity": {}}
    sa._seed_whatsapp_phone(prof, {"phone_number": "+91 98765 43210"})
    assert prof["identity"]["phone"]["value"] == "9876543210"
    # does not overwrite an existing profile phone
    prof2 = {"identity": {"phone": {"value": "9000000000"}}}
    sa._seed_whatsapp_phone(prof2, {"phone_number": "+919876543210"})
    assert prof2["identity"]["phone"]["value"] == "9000000000"
    # junk number → nothing seeded
    prof3 = {"identity": {}}
    sa._seed_whatsapp_phone(prof3, {"phone_number": "12345"})
    assert not prof3["identity"].get("phone")


def _register_ctx():
    # The customer gave a pincode THIS chat → location is confirmed-in-chat, so
    # register_enquiry may auto-create without a separate confirm step.
    return {"conv_id": 1, "channel": "whatsapp", "vertical": "fhc",
            "conv": {"custom_attributes": {}},
            "profile": {"identity": {"phone": {"value": "9876543210"}}},
            "incoming_all": ["9876543210"],
            "all_messages": [{"message_type": 0, "content": "my pincode is 560001"}]}


def test_register_enquiry_fhc_auto_creates_deal(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    monkeypatch.setattr(sa.store_locator, "upcoming", lambda *a, **k: None)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda *a, **k: {"card_name": "Studio"})
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)
    calls = []

    async def creator(conv_id, agent_name=""):
        calls.append(conv_id)
        return {"deal_id": "D9", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)

    r = asyncio.run(sa._sk_register_enquiry(
        _register_ctx(), category="fhc", city="Mumbai"))
    assert r["registered"] is True and r.get("deal_created") is True and calls == [1]
    assert "deal-ready" in fake.labels


def test_register_enquiry_doors_auto_creates_with_store_card(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    monkeypatch.setattr(sa.store_locator, "upcoming", lambda *a, **k: None)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda vert, **k: {
        "card_name": "Durian Doors — Bengaluru", "address": "MG Road, Bengaluru",
        "map_url": "https://maps.app.goo.gl/d"} if vert == "doors" else None)
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)
    called = []

    async def creator(conv_id, agent_name=""):
        called.append(conv_id)
        return {"deal_id": "D9", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)

    r = asyncio.run(sa._sk_register_enquiry(
        dict(_register_ctx(), vertical="doors"), category="doors", city="Bengaluru"))
    assert r["registered"] is True and r.get("deal_created") is True and called == [1]
    assert "Durian Doors" in r["store_card"] and "maps.app.goo.gl" in r["store_card"]


def test_system_prompt_is_channel_aware():
    from datetime import datetime
    wa = sa._system_prompt("dm", "Durian FHC WhatsApp", "fhc", datetime(2026, 1, 1),
                           "P", "T", 1, channel="whatsapp")
    assert "agent on WhatsApp" in wa and "NEVER ask for a phone" in wa and "4096 characters" in wa
    ig = sa._system_prompt("dm", "durian_furniture", "furniture", datetime(2026, 1, 1),
                           "P", "T", 1, channel="instagram")
    assert "agent on Instagram" in ig and "1000 characters" in ig and "NEVER ask for a phone" not in ig


# ── book_store_visit (store-visit appointment, all verticals) ────────────────

class _VisitFakeChatwoot:
    def __init__(self, conv_after):
        self.labels, self.notes, self.merged = [], [], []
        self._conv_after = conv_after

    async def merge_custom_attributes(self, conv_id, attrs):
        self.merged.append(attrs)

    async def add_label(self, conv_id, label):
        self.labels.append(label)

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)

    async def get_conversation(self, conv_id):
        return self._conv_after


def _visit_env(monkeypatch, *, enabled=True, store=None, deal_in_ca=True,
               contact_id="C7", creator_deal="D9"):
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_STORE_VISIT_ENABLED", enabled)
    resolved = store if store is not None else {
        "card_name": "Furniture - Bengaluru - Indiranagar", "crm_owner_id": "OWN1", "city": "Bengaluru"}
    monkeypatch.setattr(sa.store_locator, "resolve", lambda *a, **k: resolved)
    ca_now = {"crm_deal_id": "D1", "crm_contact_id": contact_id} if deal_in_ca \
        else {"crm_contact_id": contact_id}
    fake = _VisitFakeChatwoot({"custom_attributes": ca_now})
    monkeypatch.setattr(sa, "chatwoot", fake)

    events = []

    async def create_event(title, start, end, **kw):
        events.append({"title": title, "start": start, "end": end, **kw})
        return {"id": "E1"}
    monkeypatch.setattr(sa.zoho_crm, "create_event", create_event)
    monkeypatch.setattr(sa.zoho_crm, "event_url", lambda eid: f"https://crm/ev/{eid}")

    async def creator(conv_id, agent_name=""):
        return {"deal_id": creator_deal, "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)
    return fake, events


def _visit_ctx(ca=None):
    return {"conv_id": 1, "channel": "instagram", "vertical": "furniture",
            "conv": {"custom_attributes": ca if ca is not None else {"crm_deal_id": "D1"},
                     "meta": {"sender": {"name": "Asha"}}},
            "profile": {"identity": {"phone": {"value": "9876543210"},
                                     "name": {"value": "Asha"}}},
            "incoming_all": ["yes book it"]}


def test_book_store_visit_happy_path(monkeypatch):
    import asyncio
    fake, events = _visit_env(monkeypatch)
    r = asyncio.run(sa.SKILLS["book_store_visit"]["handler"](
        _visit_ctx(), date="2099-10-17", slot="morning", people="2", pincode="560038"))
    assert r["booked"] is True and r["store"] == "Furniture - Bengaluru - Indiranagar"
    assert len(events) == 1
    ev = events[0]
    assert ev["owner_id"] == "OWN1" and ev["host_id"] == "OWN1"       # the store owns the meeting
    assert ev["what_id"] == "D1" and ev["who_id"] == "C7"             # linked to deal + contact
    assert ev["start"].startswith("2099-10-17T11:00")                # morning = 11:00 IST
    assert "store-visit-booked" in fake.labels
    assert any("store_visit" in m for m in fake.merged)
    assert fake.notes and "Store visit booked" in fake.notes[0] and "9876543210" in fake.notes[0]


def test_book_store_visit_disabled_is_inert(monkeypatch):
    import asyncio
    fake, events = _visit_env(monkeypatch, enabled=False)
    r = asyncio.run(sa.SKILLS["book_store_visit"]["handler"](_visit_ctx(), date="2099-10-17"))
    assert r["booked"] is False and "not enabled" in r["note"] and not events and not fake.notes


def test_book_store_visit_needs_a_valid_future_date(monkeypatch):
    import asyncio
    _, events = _visit_env(monkeypatch)
    assert asyncio.run(sa.SKILLS["book_store_visit"]["handler"](_visit_ctx(), date=""))["need_date"]
    assert asyncio.run(sa.SKILLS["book_store_visit"]["handler"](
        _visit_ctx(), date="2000-01-01"))["booked"] is False          # past
    assert not events


def test_book_store_visit_no_owner_cannot_calendar(monkeypatch):
    import asyncio
    _, events = _visit_env(monkeypatch, store={"card_name": "X", "crm_owner_id": ""})
    r = asyncio.run(sa.SKILLS["book_store_visit"]["handler"](_visit_ctx(), date="2099-10-17"))
    assert r["booked"] is False and "owner" in r["note"] and not events


def test_book_store_visit_creates_deal_when_missing(monkeypatch):
    import asyncio
    # No crm_deal_id in ca → the skill calls the deal creator; the refreshed
    # conversation then carries the new id for the event link.
    fake, events = _visit_env(monkeypatch, deal_in_ca=False)
    fake._conv_after = {"custom_attributes": {"crm_deal_id": "D9", "crm_contact_id": "C7"}}
    r = asyncio.run(sa.SKILLS["book_store_visit"]["handler"](
        _visit_ctx(ca={}), date="2099-10-17"))
    assert r["booked"] is True and events[0]["what_id"] == "D9"


def test_book_store_visit_no_deal_possible_defers(monkeypatch):
    import asyncio
    fake, events = _visit_env(monkeypatch, deal_in_ca=False, creator_deal="")
    fake._conv_after = {"custom_attributes": {}}
    r = asyncio.run(sa.SKILLS["book_store_visit"]["handler"](
        _visit_ctx(ca={}), date="2099-10-17"))
    assert r["booked"] is False and "register the enquiry first" in r["note"] and not events


# ── Turn-budget handoff is opt-in (default off) ──────────────────────────────

def test_handoff_budget_disabled_by_default(monkeypatch):
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_HANDOFF_AFTER", 0)
    assert sa._handoff_over_budget(1) is False
    assert sa._handoff_over_budget(500) is False          # long/returning convo keeps engaging
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_HANDOFF_AFTER", -5)
    assert sa._handoff_over_budget(500) is False


def test_handoff_budget_enabled_when_positive(monkeypatch):
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_HANDOFF_AFTER", 25)
    assert sa._handoff_over_budget(25) is False
    assert sa._handoff_over_budget(26) is True


# ── Engagement-scoped message count (converge + optional handoff) ────────────

def _inc(text="hi"):
    return {"message_type": 0, "content": text}


def _resolved_activity():
    return {"message_type": 2, "content": "Conversation was marked resolved by Aditya"}


def test_engagement_count_resets_after_resolve():
    # 6 old messages, resolved, then 1 new "Hii" → the current engagement is 1,
    # not 7, so converge/handoff see a fresh conversation.
    msgs = [_inc() for _ in range(6)] + [_resolved_activity()] + [_inc("Hii")]
    assert sa._engagement_incoming_count(msgs) == 1


def test_engagement_count_whole_history_when_never_resolved():
    msgs = [_inc() for _ in range(4)]
    assert sa._engagement_incoming_count(msgs) == 4


def test_engagement_count_counts_after_the_LAST_resolve():
    msgs = ([_inc()] + [_resolved_activity()] + [_inc(), _inc()]
            + [_resolved_activity()] + [_inc("latest")])
    assert sa._engagement_incoming_count(msgs) == 1


def test_engagement_count_never_zero():
    assert sa._engagement_incoming_count([_resolved_activity()]) == 1
    assert sa._engagement_incoming_count([]) == 1


# ── Engage-first + confirm-city + never-silent-after-write (PR #263) ─────────

def _conv_msgs(*texts, resolved_before=False):
    msgs = []
    if resolved_before:
        msgs += [{"message_type": 0, "content": "old enquiry"},
                 {"message_type": 2, "content": "Conversation was marked resolved by X"}]
    msgs += [{"message_type": 0, "content": t} for t in texts]
    return msgs


def test_location_given_this_chat():
    ctx = lambda msgs: {"all_messages": msgs}
    assert sa._location_given_this_chat(ctx(_conv_msgs("I'm in 560001")), "Bengaluru") is True
    assert sa._location_given_this_chat(ctx(_conv_msgs("I'm in New Delhi")), "New Delhi") is True
    # city only from records (never typed this chat) → must be confirmed
    assert sa._location_given_this_chat(ctx(_conv_msgs("Hello", "modular kitchen")), "New Delhi") is False
    # a pincode given BEFORE a resolve doesn't count for the new engagement
    assert sa._location_given_this_chat(
        {"all_messages": _conv_msgs("hi", resolved_before=True) + []}, "Delhi") is False


def test_register_enquiry_confirms_a_records_only_city(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    monkeypatch.setattr(sa.store_locator, "upcoming", lambda *a, **k: None)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda *a, **k: {"card_name": "Studio"})
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)
    created = []

    async def creator(conv_id, agent_name=""):
        created.append(conv_id)
        return {"deal_id": "D9", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)
    # City only from records (customer said just "Hello" + opener this chat)
    ctx = {"conv_id": 1, "channel": "whatsapp", "vertical": "fhc",
           "conv": {"custom_attributes": {}},
           "profile": {"identity": {"phone": {"value": "9876543210"}}},
           "incoming_all": ["Hello", "modular kitchen"],
           "all_messages": _conv_msgs("Hello", "I want to ask about modular kitchen")}
    r = asyncio.run(sa._sk_register_enquiry(ctx, category="fhc", city="New Delhi"))
    assert r["registered"] is False and r["confirm_location"] is True
    assert not created and "deal-ready" not in fake.labels          # nothing written yet
    # after the customer confirms → confirm=true proceeds and creates
    r2 = asyncio.run(sa._sk_register_enquiry(ctx, category="fhc", city="New Delhi", confirm=True))
    assert r2["registered"] is True and r2.get("deal_created") is True and created == [1]


def test_wrote_this_turn_forces_a_send():
    reg = [{"name": "register_enquiry", "result": {"registered": True, "deal_created": True}}]
    assert sa._wrote_this_turn("Your enquiry is registered.", reg) is True
    assert sa._wrote_this_turn("", reg) is False                    # no reply → can't send
    assert sa._wrote_this_turn("hi", [{"name": "find_showrooms", "result": {"resolved": True}}]) is False
    assert sa._wrote_this_turn("booked", [{"name": "book_store_visit", "result": {"booked": True}}]) is True


# ── FHC deal confirmation sends the studio address (PR: feat/fhc-deal-store-ack) ──

def _fhc_reg_ctx():
    return {"conv_id": 1, "channel": "whatsapp", "vertical": "fhc",
            "conv": {"custom_attributes": {}},
            "profile": {"identity": {"phone": {"value": "9876543210"}}},
            "incoming_all": ["my pincode is 110015"],
            "all_messages": [{"message_type": 0, "content": "my pincode is 110015"}]}


def test_fhc_deal_confirmation_includes_studio_card(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    monkeypatch.setattr(sa.store_locator, "upcoming", lambda *a, **k: None)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda *a, **k: {
        "card_name": "Durian FHC — Delhi - Kirti Nagar", "address": "A-1 Kirti Nagar, New Delhi",
        "timing": "10am-8pm", "map_url": "https://maps.app.goo.gl/xyz"})
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)

    async def creator(conv_id, agent_name=""):
        return {"deal_id": "D9", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)

    r = asyncio.run(sa._sk_register_enquiry(
        _fhc_reg_ctx(), category="fhc", city="New Delhi"))
    assert r["deal_created"] is True
    assert "Kirti Nagar" in r["store_card"] and "maps.app.goo.gl" in r["store_card"]
    assert "share the store/studio" in r["next"]


def test_fhc_deal_no_studio_in_range_stays_plain(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    monkeypatch.setattr(sa.store_locator, "upcoming", lambda *a, **k: None)
    # no FHC studio for this city (out of coverage → Customer Support)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda *a, **k: None)
    fake = _DealFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)

    async def creator(conv_id, agent_name=""):
        return {"deal_id": "D9", "created": True}
    monkeypatch.setattr(sa, "_deal_creator", creator)

    # city given this chat (confirm gate) but resolve→None means no studio card
    ctx = dict(_fhc_reg_ctx(), all_messages=[{"message_type": 0, "content": "Imphal"}],
               incoming_all=["Imphal"])
    r = asyncio.run(sa._sk_register_enquiry(ctx, category="fhc", city="Imphal"))
    assert r["deal_created"] is True and "store_card" not in r
    assert "our team will reach out" in r["next"]


# ── FHC guidance follows the VERTICAL, not the inbox name (conv #9916) ───────

def test_fhc_rule_present_on_whatsapp_fhc_inbox_despite_name():
    from datetime import datetime
    # WhatsApp FHC inbox is named "Whatsapp FHC" (not matching _is_fhc_account),
    # but its vertical is fhc → the FHC rule MUST still be injected.
    assert sa._is_fhc_account("Whatsapp FHC") is False
    p = sa._system_prompt("dm", "Whatsapp FHC", "fhc", datetime(2026, 1, 1),
                          "P", "T", 1, channel="whatsapp")
    assert "FULL HOME CUSTOMISATION ACCOUNT" in p
    assert "route_to_showroom" not in p
    assert "ALREADY HAVE it in full" in p                 # phone clarity
    # a furniture inbox gets neither
    f = sa._system_prompt("dm", "Durian Furniture", "furniture", datetime(2026, 1, 1),
                          "P", "T", 1, channel="instagram")
    assert "FULL HOME CUSTOMISATION ACCOUNT" not in f


# ── Part 2: one enquiry skill, the product line comes from the conversation ──

def test_norm_line():
    assert [sa._norm_line(x) for x in ("FHC", "full home", "Full_Home_Customization", "interiors")] \
        == ["fhc"] * 4
    assert [sa._norm_line(x) for x in ("doors", "Door", "veneer")] == ["doors"] * 3
    assert [sa._norm_line(x) for x in ("furniture", "product_enquiry")] == ["furniture"] * 2
    assert sa._norm_line("") == "" and sa._norm_line("something") == "" and sa._norm_line(None) == ""


def test_person_name_vs_handle():
    for real in ("Rajeshri Patil", "Ms. Rajeshri", "Shilpa", "Aman singh"):
        assert sa._looks_like_person_name(real), real
    for not_name in ("house_of_holani", "rv_1990", "Test Customer", "Durian Furniture",
                     "n.m.singh_rajput", "", "x" * 50, "S.Kumar99"):
        assert not sa._looks_like_person_name(not_name), not_name


def _front_ctx(incoming, contact="Test Customer", profile=None, ca=None, msg_id=10):
    return {"conv_id": 1, "channel": "instagram", "vertical": "furniture",
            "conv": {"custom_attributes": dict(ca or {}),
                     "meta": {"sender": {"id": 5, "name": contact}}},
            "profile": profile or {"identity": {}, "location": {}},
            "incoming_all": list(incoming),
            "all_messages": [{"message_type": 0, "content": t} for t in incoming],
            "latest_msg_id": msg_id}


def _front_setup(monkeypatch):
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_AUTO_DEAL", True)
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_DEAL_NOTES", True)
    monkeypatch.setattr(sa.config, "STORE_LOCATOR_ENABLED", True)
    monkeypatch.setattr(sa.store_locator, "upcoming", lambda *a, **k: None)
    monkeypatch.setattr(sa.store_locator, "resolve", lambda vert, pincode=None, city=None: {
        "card_name": f"{vert} store", "city": "Delhi", "crm_owner_id": "77",
        "address": "A-1 Kirti Nagar", "email": "s@d.in"})
    fake = _RouteFakeChatwoot()
    monkeypatch.setattr(sa, "chatwoot", fake)
    deals, notes = [], []

    async def creator(conv_id, agent_name="", allow_duplicate=False):
        deals.append(allow_duplicate)
        return {"deal_id": f"D{len(deals)}", "created": True}

    async def note(module, record_id, title, body):
        notes.append((record_id, body))
    monkeypatch.setattr(sa, "_deal_creator", creator)
    monkeypatch.setattr(sa.zoho_crm, "create_note", note)
    return fake, deals, notes


def test_register_says_exactly_what_is_missing(monkeypatch):
    import asyncio
    fake, deals, _ = _front_setup(monkeypatch)
    reg = sa.SKILLS["register_enquiry"]["handler"]
    # nothing known: all four pieces come back in one list, nothing is written
    r = asyncio.run(reg(_front_ctx(["I want to buy a sofa"])))
    assert r["registered"] is False and r["need_phone"] and len(r["missing"]) == 4
    assert "ONE message" in r["note"] and not deals and not fake.merged
    # line + phone + name typed: only the location is asked for
    ctx = _front_ctx(["I need 5 veneer doors. I am Rahul Verma, my number is 9560150835"])
    r = asyncio.run(reg(ctx, line="doors", name="Rahul Verma", phone="9560150835"))
    assert r["missing"] == ["their city or pincode"] and "need_phone" not in r and not deals


def test_register_creates_for_every_line_with_one_message(monkeypatch):
    import asyncio
    reg = sa.SKILLS["register_enquiry"]["handler"]
    for line, cat in (("furniture", "product_enquiry"), ("doors", "doors_veneer_plywood"),
                      ("fhc", "full_home_customization")):
        fake, deals, _ = _front_setup(monkeypatch)
        ctx = _front_ctx(["I am Rahul Verma, 9560150835, pincode 110054"])
        r = asyncio.run(reg(ctx, line=line, name="Rahul Verma", phone="9560150835",
                            pincode="110054"))
        ca = ctx["conv"]["custom_attributes"]
        assert r["registered"] is True and r["deal_created"] is True and deals == [False], line
        assert ca["phase2_category"] == cat and ca["crm_deals"] == {line: "D1"}
        assert ca["enquiry_customer_name"] == "Rahul Verma" and ca["enquiry_line"] == line
        # furniture tags the showroom owner; doors / FHC never do
        assert bool(ca.get("retail_deal_owner")) == (line == "furniture")


def test_profile_name_is_confirmed_in_a_later_message(monkeypatch):
    import asyncio
    fake, deals, _ = _front_setup(monkeypatch)
    reg = sa.SKILLS["register_enquiry"]["handler"]
    ctx = _front_ctx(["buy the lewis sofa. 9560150835, pincode 110054"], contact="Rajeshri Patil")
    args = dict(line="furniture", phone="9560150835", pincode="110054")
    r = asyncio.run(reg(ctx, **args))
    assert r["registered"] is False and r["confirm_name"] == "Rajeshri Patil" and not r["missing"]
    assert "Shall we register this under the name Rajeshri Patil?" in r["note"] and not deals
    # the agent cannot confirm it for itself in the same turn
    r = asyncio.run(reg(ctx, name="Rajeshri Patil", name_confirmed=True, **args))
    assert r["registered"] is False and r["confirm_name"] and not deals
    # the customer's next message is the yes → the deal goes ahead under that name
    ctx["latest_msg_id"] = 11
    ctx["incoming_all"].append("Yes")
    r = asyncio.run(reg(ctx, name="Rajeshri Patil", name_confirmed=True, **args))
    assert r["deal_created"] is True and deals == [False]
    assert ctx["conv"]["custom_attributes"]["enquiry_customer_name"] == "Rajeshri Patil"


def test_name_typed_handle_or_corrected(monkeypatch):
    import asyncio
    fake, deals, _ = _front_setup(monkeypatch)
    reg = sa.SKILLS["register_enquiry"]["handler"]
    args = dict(line="furniture", phone="9560150835", pincode="110054")
    # a handle is not a name → ask, never confirm, never use it
    r = asyncio.run(reg(_front_ctx(["sofa 9560150835 110054"], contact="house_of_holani"), **args))
    assert r["missing"] == ["their full name"] and "confirm_name" not in r and not deals
    # an invented name (not typed, not the profile's) is not accepted either
    r = asyncio.run(reg(_front_ctx(["sofa 9560150835 110054"], contact="house_of_holani"),
                        name="Rahul Verma", **args))
    assert r["missing"] == ["their full name"] and not deals
    # typed by the customer → no question, even when the profile shows another name
    ctx = _front_ctx(["No, register it under Anita Patil. 9560150835, 110054"],
                     contact="Rajeshri Patil")
    r = asyncio.run(reg(ctx, name="Anita Patil", **args))
    assert r["deal_created"] is True and deals == [False]
    assert ctx["conv"]["custom_attributes"]["enquiry_customer_name"] == "Anita Patil"


def test_one_deal_per_line(monkeypatch):
    import asyncio
    fake, deals, notes = _front_setup(monkeypatch)
    reg = sa.SKILLS["register_enquiry"]["handler"]
    ctx = _front_ctx(["I am Rahul Verma, 9560150835, pincode 110054"])
    who = dict(name="Rahul Verma", phone="9560150835", pincode="110054")
    assert asyncio.run(reg(ctx, line="furniture", **who))["deal_created"] is True
    ca = ctx["conv"]["custom_attributes"]
    # same line again (a chair after the sofa): no second deal; the detail is noted on it
    r = asyncio.run(reg(ctx, line="furniture", requirement="also 4 dining chairs", **who))
    assert deals == [False] and notes == [("D1", "also 4 dining chairs")]
    # a different line is a new enquiry: a second deal, asked for explicitly
    r = asyncio.run(reg(ctx, line="doors", requirement="5 veneer doors", **who))
    assert r["deal_created"] is True and deals == [False, True]
    assert ca["crm_deals"] == {"furniture": "D1", "doors": "D2"} and ca["crm_deal_id"] == "D2"
    assert ca["phase2_category"] == "doors_veneer_plywood"
    # doors again: already registered, nothing new created, the detail goes onto D2
    r = asyncio.run(reg(ctx, line="doors", requirement="make it 6 doors", **who))
    assert r["already_registered"] and deals == [False, True] and notes[-1] == ("D2", "make it 6 doors")
    # and a third line still gets its own
    assert asyncio.run(reg(ctx, line="fhc", **who))["deal_created"] is True
    assert deals == [False, True, True] and set(ca["crm_deals"]) == {"furniture", "doors", "fhc"}


def test_old_conversation_deal_line_is_read_from_its_category():
    assert sa._deal_lines({"crm_deal_id": "X", "phase2_category": "doors_veneer_plywood"}) == \
        {"doors": "X"}
    assert sa._deal_lines({"crm_deal_id": "X"}) == {"furniture": "X"}
    assert sa._deal_lines({"crm_deal_id": "X", "crm_deals": {"fhc": "Y"}}) == {"fhc": "Y"}
    assert sa._deal_lines({}) == {}


def test_later_details_become_a_note_on_the_deal(monkeypatch):
    import asyncio
    fake, deals, notes = _front_setup(monkeypatch)
    learn = [{"op": "learn", "kind": "interest", "what": "modular kitchen", "quote": "a kitchen"},
             {"op": "learn", "kind": "budget", "what": "around 6 lakh", "quote": "6 lakh"},
             {"op": "learn", "kind": "routed", "what": "Delhi"},          # not a customer detail
             {"op": "set", "field": "location.city", "value": "Delhi"}]
    ctx = _front_ctx(["..."], ca={"crm_deal_id": "D7"})
    asyncio.run(sa._note_deal_updates(ctx, learn, []))
    assert len(notes) == 1 and notes[0][0] == "D7"
    assert "modular kitchen" in notes[0][1] and "6 lakh" in notes[0][1] and "routed" not in notes[0][1]
    asyncio.run(sa._note_deal_updates(ctx, learn, []))                    # one note per turn
    assert len(notes) == 1
    # not on the turn the deal is created, not without a deal, not for nothing
    created = [{"name": "register_enquiry", "result": {"deal_created": True}}]
    asyncio.run(sa._note_deal_updates(_front_ctx(["."], ca={"crm_deal_id": "D7"}), learn, created))
    asyncio.run(sa._note_deal_updates(_front_ctx(["."]), learn, []))
    asyncio.run(sa._note_deal_updates(_front_ctx(["."], ca={"crm_deal_id": "D7"}), [], []))
    assert len(notes) == 1
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_DEAL_NOTES", False)      # kill switch
    asyncio.run(sa._note_deal_updates(_front_ctx(["."], ca={"crm_deal_id": "D7"}), learn, []))
    assert len(notes) == 1


def test_reask_detector_counts_a_name_only_when_held():
    prof = {"identity": {"phone": {"value": "9"}, "name": {"value": "Rahul Verma"}},
            "location": {"city": {"value": "Delhi"}}}
    assert sa.reasks_known_details("Could you share your full name?", prof)
    del prof["identity"]["name"]
    assert not sa.reasks_known_details("Could you share your full name?", prof)


def test_retail_owner_never_owns_a_doors_or_fhc_deal(monkeypatch):
    """One conversation can hold a deal per product line, so a furniture
    showroom picked earlier must not become the owner of the doors / FHC deal."""
    import asyncio
    import main

    async def doors(*a, **k):
        return {"configured": True, "owner_id": "D", "location": "doors-other", "vertical": "Doors"}

    async def named(*a, **k):
        return {"configured": True, "owner_id": "H", "location": "homestudio:Delhi", "vertical": "FHC"}
    monkeypatch.setattr(main, "_resolve_doors_owner", doors)
    monkeypatch.setattr(main, "_resolve_named_owner", named)
    showroom = {"owner_id": "9", "location": "Delhi - Kirti Nagar"}

    def owner(category):
        return asyncio.run(main._resolve_deal_owner(
            {"retail_deal_owner": showroom, "phase2_category": category}, "", "", ""))["owner_id"]
    assert owner("product_enquiry") == "9"
    assert owner("doors_veneer_plywood") == "D"
    assert owner("full_home_customization") == "H"


def test_product_is_asked_once_when_only_the_line_is_known():
    for vague in ("", "doors", "Furniture", "full home customisation"):
        assert sa._vague_requirement(vague), vague
    for clear in ("5 veneer doors", "modular kitchen", "Lewis corner sofa"):
        assert not sa._vague_requirement(clear), clear
    made = {"registered": True, "deal_created": True, "next": "tell them it is registered."}
    assert "ONE question" in sa._ask_product_once(dict(made), "doors")["next"]
    assert "ONE question" not in sa._ask_product_once(dict(made), "5 veneer doors")["next"]
    # nothing is asked when no deal was created (the missing-details ask comes first)
    assert "next" not in sa._ask_product_once({"registered": False}, "")


def test_enquiry_checklist_lists_everything_still_missing():
    """The agent sees, every turn, what an enquiry still needs — so it can ask
    for all of it in one message before register_enquiry has ever been called."""
    conv = {"custom_attributes": {}, "meta": {"sender": {"name": "house_of_holani"}}}
    block = sa._enquiry_checklist({"identity": {}, "location": {}}, conv)
    assert "Still missing: their contact number; their city or pincode; their full name." in block
    assert "EVERYTHING still missing in that one message" in block and "nothing yet" in block
    # WhatsApp: the number is on the profile; a real-looking profile name is to be confirmed
    conv = {"custom_attributes": {}, "meta": {"sender": {"name": "Rajeshri Patil"}}}
    block = sa._enquiry_checklist({"identity": {"phone": {"value": "9560150835"}}, "location": {}}, conv)
    assert "We hold: their contact number." in block and "their contact number;" not in block
    assert "Shall we register this under the name Rajeshri Patil?" in block
    # everything in hand, one line already registered
    conv = {"custom_attributes": {"enquiry_customer_name": "Rahul Verma", "crm_deal_id": "D1",
                                  "phase2_category": "doors_veneer_plywood"},
            "meta": {"sender": {"name": "rv_1990"}}}
    block = sa._enquiry_checklist({"identity": {"phone": {"value": "9"}},
                                   "location": {"pincode": {"value": "110054"}}}, conv)
    assert "Nothing is missing" in block and "Still missing" not in block
    assert "Already registered in this conversation: doors" in block and "Rahul Verma" in block


# ── Click-to-WhatsApp ad referral: context to the agent + onto the deal ──────

def _msg_with_referral(ref):
    return {"message_type": 0, "content": "Hello! Can I get more information on this?",
            "content_attributes": {"referral": ref}}


def test_campaign_referral_and_block():
    ref = {"headline": "Grand Festive Offer", "body": "Flat 50% off select furniture",
           "source_url": "https://www.durian.in/furniture", "source_id": "120x"}
    msgs = [{"message_type": 2, "content": "activity"}, _msg_with_referral(ref),
            {"message_type": 0, "content": "yes"}]
    assert sa._campaign_referral(msgs) == ref
    assert sa._campaign_referral([{"message_type": 0, "content": "hi"}]) == {}
    block = sa._campaign_block(ref)
    assert "CAMPAIGN CONTEXT" in block and "Grand Festive Offer" in block
    assert "durian.in/furniture" in block and "do NOT ask them to send a screenshot" in block
    assert sa._campaign_block({}) == ""


def test_system_prompt_injects_campaign_only_when_passed():
    from datetime import datetime
    base = dict(surface="dm", inbox="Whatsapp FHC", vertical="fhc",
                now=datetime(2026, 1, 1), profile_block="P", templates="T",
                n_customer_msgs=1, channel="whatsapp")
    assert "CAMPAIGN CONTEXT" not in sa._system_prompt(**base)
    assert "CAMPAIGN CONTEXT" in sa._system_prompt(**base, campaign=sa._campaign_block(
        {"headline": "Grand Festive Offer", "source_url": "https://www.durian.in/furniture"}))


def test_main_campaign_referral_parses_dict_and_json():
    import json as _json
    import main
    ref = {"headline": "Grand Festive Offer", "source_url": "https://www.durian.in/x"}
    assert main._campaign_referral({"referral": ref}) == ref
    assert main._campaign_referral(_json.dumps({"referral": ref})) == ref   # legacy string form
    assert main._campaign_referral({"foo": 1}) == {}
    assert main._campaign_referral(None) == {}
    assert main._campaign_referral("not json") == {}


def test_outbound_campaign_block_and_injection():
    from datetime import datetime
    conv = {"additional_attributes": {"reply_campaign": {
        "title": "Grand Festive Offer", "message": "Flat 50% off select furniture"}}}
    block = sa._outbound_campaign_block(conv)
    assert "CAMPAIGN CONTEXT" in block and "Grand Festive Offer" in block
    assert "Flat 50% off" in block and "do NOT ask what 'this' is" in block
    assert sa._outbound_campaign_block({"additional_attributes": {}}) == ""
    # it reaches the prompt via the campaign arg
    base = dict(surface="dm", inbox="Whatsapp FHC", vertical="fhc",
                now=datetime(2026, 1, 1), profile_block="P", templates="T",
                n_customer_msgs=1, channel="whatsapp")
    assert "Grand Festive Offer" in sa._system_prompt(**base, campaign=block)


# --- Deal-details gate: accumulate prior details (no re-asking) ---------------

def test_deal_gate_remembers_prior_phone(monkeypatch):
    import asyncio
    import main
    merges = []

    class _CW:
        async def merge_custom_attributes(self, cid, attrs):
            merges.append(attrs)

        async def remove_label(self, cid, label):
            pass

    monkeypatch.setattr(main, "chatwoot", _CW())
    monkeypatch.setattr(main, "_extract_phones", lambda text, cap=3: [])   # reply has no phone

    async def _llm(name, text, have_phone):
        return {"city": "Chhatarpur", "ask_reply": "" if have_phone else "please share your phone"}
    monkeypatch.setattr(main, "_deal_details_gate_llm", _llm)

    async def _noop(*a, **k):
        pass
    monkeypatch.setattr(main, "_retail_send", _noop)
    monkeypatch.setattr(main, "_label_conversation", _noop)
    monkeypatch.setattr(main, "_maybe_auto_create_deal", _noop)
    monkeypatch.setattr(main, "_EMAIL_CUSTOMER_ACK_ENABLED", True)

    # Phone was given earlier (first email's signature) → carried in `prior`;
    # this reply only gives the city. The gate must capture BOTH, not re-ask phone.
    audit = asyncio.run(main._run_deal_details_gate(
        1, "Abhinav", "a@x.com", "Re: enquiry", "We are based in Chhatarpur.",
        "project_bulk_order", attempt=1, channel="email",
        prior={"phone": "7898632017", "city": None}))
    captured = next((m["deal_customer_details"] for m in merges if m.get("deal_customer_details")), None)
    assert captured and captured["phone"] == "7898632017" and captured["city"] == "Chhatarpur"
    assert any("deal-ready" in line.lower() or "acknowledged" in line.lower() for line in audit)


def test_deal_gate_asks_when_city_still_missing_and_stores_phone(monkeypatch):
    import asyncio
    import main
    merges = []

    class _CW:
        async def merge_custom_attributes(self, cid, attrs):
            merges.append(attrs)

        async def remove_label(self, cid, label):
            pass

    monkeypatch.setattr(main, "chatwoot", _CW())
    monkeypatch.setattr(main, "_extract_phones", lambda text, cap=3: ["7898632017"])   # phone in signature

    async def _llm(name, text, have_phone):
        return {"city": "", "ask_reply": "please share your city"}
    monkeypatch.setattr(main, "_deal_details_gate_llm", _llm)

    async def _noop(*a, **k):
        pass
    monkeypatch.setattr(main, "_retail_send", _noop)
    monkeypatch.setattr(main, "_label_conversation", _noop)
    monkeypatch.setattr(main, "_maybe_auto_create_deal", _noop)
    monkeypatch.setattr(main.config, "DEAL_DETAILS_MAX_ASKS", 3)

    # First email: phone present, city missing → asks city, and REMEMBERS the phone
    # in pending_deal_details so the next reply doesn't re-ask it.
    asyncio.run(main._run_deal_details_gate(
        1, "Abhinav", "a@x.com", "Enquiry", "...CONTACT NO 7898632017",
        "project_bulk_order", attempt=0, channel="email"))
    pending = next((m["pending_deal_details"] for m in merges
                    if m.get("pending_deal_details")), None)
    assert pending and pending["phone"] == "7898632017" and not pending["city"]
    assert not any(m.get("deal_customer_details") for m in merges)   # not captured yet


# --- Ticket-decision card only for ticket-worthy categories -------------------

def test_ticket_card_only_for_ticket_worthy_category(monkeypatch):
    import asyncio
    import main
    category = {"v": "project_bulk_order"}

    class _CW:
        async def get_conversation(self, cid):
            return {"custom_attributes": {"email_category_v2": {"category": category["v"]}},
                    "meta": {"sender": {"email": "a@x.com"}}, "inbox_id": 777}

    monkeypatch.setattr(main, "chatwoot", _CW())
    monkeypatch.setattr(main.config, "REVIEWS_INBOX_ID", 0)
    monkeypatch.setattr(main.config, "TICKET_DECISION_CATEGORIES", {"complaint", "legal_complaint"})
    monkeypatch.setattr(main.config, "ZOHO_TICKET_REQUIRE_APPROVAL", True)
    monkeypatch.setattr(main, "_is_comment_conversation", lambda c: False)
    paused = []

    async def _pause(cid, email, label):
        paused.append(label)
        return {"awaiting_approval": True}
    monkeypatch.setattr(main, "_pause_ticket_for_approval", _pause)

    data = {"conversation": {"id": 1, "status": "open"}}
    # Deal/bulk category → no ticket card
    res = asyncio.run(main.handle_status_changed(data))
    assert res.get("reason") == "category_not_ticket_worthy" and not paused

    # Complaint → the card is surfaced
    category["v"] = "complaint"
    asyncio.run(main.handle_status_changed(data))
    assert paused == ["manual_handoff"]
