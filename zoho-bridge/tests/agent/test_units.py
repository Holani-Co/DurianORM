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


def test_fhc_deal_owner_is_nearest_fhc_studio_else_pratik(monkeypatch):
    """FHC deals stay in the FHC vertical: pincode/city → nearest dedicated FHC
    studio; no studio in range → Pratik Ojha (Goregaon login), never Customer
    Support - Marketing (convs 9920 / 10825 / 11078)."""
    import asyncio
    import main
    monkeypatch.setattr(main.config, "STORE_LOCATOR_ENABLED", True)
    monkeypatch.setattr(main.store_locator, "_override_doc", lambda: {})

    async def central(*a, **k):
        return main._fallback_owner()
    monkeypatch.setattr(main, "_resolve_named_owner", central)

    def owner(details, body=""):
        return asyncio.run(main._resolve_deal_owner(
            {"phase2_category": "full_home_customization",
             "deal_customer_details": details}, body, "", ""))

    pratik = "3608871000315511062"
    assert main.fhc_fallback_owner()["owner_id"] == pratik
    assert owner({"city": "110054"})["owner_id"] == "3608871000410312080"        # Delhi → Kirti Nagar FHC
    assert owner({"city": "Ghaziabad", "pincode": "201010"})["owner_id"] == "3608871000484244133"  # → Noida FHC
    assert owner({"city": "Shillong", "pincode": "793001"})["owner_id"] == pratik  # no studio in range
    assert owner({})["owner_id"] == pratik                                        # AI found nothing
    # only dedicated FHC studios own FHC deals — never a furniture/doors store
    hit = main.store_locator.resolve("fhc", pincode="110015", primary_only=True)
    assert hit["crm_owner_id"] == "3608871000410312080"


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


# --- Agent-only campaign context note (PR follow-up) ---------------------------

class _NoteFakeChatwoot:
    def __init__(self):
        self.notes = []          # (conv_id, content) text notes
        self.media = []          # (conv_id, content, filename, ctype) media notes
        self.merged = []         # custom-attr merges

    async def post_private_note(self, conv_id, content):
        self.notes.append((conv_id, content))
        return {"id": 1}

    async def post_private_note_media(self, conv_id, content, media, filename, ctype):
        self.media.append((conv_id, content, filename, ctype, media))
        return {"id": 2}

    async def merge_custom_attributes(self, conv_id, attrs):
        self.merged.append(attrs)


def test_ad_note_body_format():
    import main
    ad = main._ad_note_body({"headline": "Grand Festive Offer", "body": "Up to 50% off",
                             "source_url": "https://durian.in/x"})
    assert "WhatsApp Ad lead" in ad and "Grand Festive Offer — Up to 50% off" in ad
    assert "durian.in/x" in ad


def _ad_data(**ref):
    return {"content_attributes": {"referral": ref}}


def test_ad_note_embeds_creative(monkeypatch):
    import asyncio
    import main
    fake = _NoteFakeChatwoot()
    monkeypatch.setattr(main, "chatwoot", fake)

    async def fake_fetch(url):
        return b"\xff\xd8\xff", "image/jpeg"
    monkeypatch.setattr(main, "_fetch_ad_creative", fake_fetch)

    conv = {"custom_attributes": {}}
    data = _ad_data(headline="Grand Festive Offer", media_type="image",
                    image_url="https://cdn.meta/x.jpg", source_url="https://durian.in/x")
    asyncio.run(main._maybe_post_ad_note(conv, 55, data))
    assert len(fake.media) == 1 and fake.media[0][2] == "ad-creative.jpg"
    assert "Grand Festive Offer" in fake.media[0][1] and not fake.notes
    assert fake.merged == [{"campaign_note_posted": True}]


def test_ad_note_falls_back_to_link_when_fetch_fails(monkeypatch):
    import asyncio
    import main
    fake = _NoteFakeChatwoot()
    monkeypatch.setattr(main, "chatwoot", fake)

    async def fake_fetch(url):
        return None, ""
    monkeypatch.setattr(main, "_fetch_ad_creative", fake_fetch)

    conv = {"custom_attributes": {}}
    asyncio.run(main._maybe_post_ad_note(conv, 7, _ad_data(headline="X",
                                                           image_url="https://cdn.meta/x.jpg")))
    assert not fake.media and len(fake.notes) == 1
    assert "Creative: https://cdn.meta/x.jpg" in fake.notes[0][1]


def test_ad_note_idempotent_and_skips_without_referral(monkeypatch):
    import asyncio
    import main
    fake = _NoteFakeChatwoot()
    monkeypatch.setattr(main, "chatwoot", fake)

    async def fake_fetch(url):
        return None, ""
    monkeypatch.setattr(main, "_fetch_ad_creative", fake_fetch)

    # already-posted conv → nothing
    asyncio.run(main._maybe_post_ad_note(
        {"custom_attributes": {"campaign_note_posted": True}}, 9,
        _ad_data(headline="X")))
    assert not fake.notes and not fake.media
    # no referral on the message → nothing (outbound-campaign case lives in Chatwoot)
    asyncio.run(main._maybe_post_ad_note({"custom_attributes": {}}, 9, {}))
    asyncio.run(main._maybe_post_ad_note(
        {"custom_attributes": {},
         "additional_attributes": {"reply_campaign": {"title": "Diwali Sale"}}}, 9, {}))
    assert not fake.notes and not fake.media


def test_ad_note_respects_disable_flag(monkeypatch):
    import asyncio
    import main
    fake = _NoteFakeChatwoot()
    monkeypatch.setattr(main, "chatwoot", fake)
    monkeypatch.setattr(main.config, "WHATSAPP_CAMPAIGN_NOTE_ENABLED", False)
    asyncio.run(main._maybe_post_ad_note({"custom_attributes": {}}, 9,
                                         _ad_data(headline="X", image_url="https://cdn.meta/x.jpg")))
    assert not fake.notes and not fake.media and not fake.merged



# --- Social partnership / pitch forwarding ------------------------------------

class _PFFakeChatwoot:
    def __init__(self):
        self.emails = []      # (to, filename, cc, has_pdf)
        self.teams = []       # assigned team ids
        self.merged = []
        self.notes = []
        self.convs = []       # created forwarding conversations

    async def get_conversation_messages(self, conv_id):
        return [{"message_type": 0, "content": "Hi, we'd love to collaborate with Durian",
                 "created_at": 1_700_000_000},
                {"message_type": 1, "content": "Could you share a bit more?",
                 "created_at": 1_700_000_100}]

    async def create_contact(self, name, identifier, inbox_id, email=None, **_):
        return 11, "src-1"

    async def create_conversation(self, source_id, inbox_id, contact_id, **_):
        self.convs.append((inbox_id, source_id))
        return 999

    async def send_email_with_attachment(self, conv_id, content, to_emails, filename,
                                         data, ctype, cc_emails=None):
        self.emails.append((to_emails, filename, cc_emails, data[:4] == b"%PDF", content))
        return {"id": 1}

    async def find_team_id(self, name):
        return {"Marketing /Advertising": 3, "Collaboration Request": 10}.get(name)

    async def assign_team(self, conv_id, team_id):
        self.teams.append(team_id)

    async def merge_custom_attributes(self, conv_id, attrs):
        self.merged.append(attrs)

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)


_PF_RULES = {"categories": {
    "marketing_advertising": {"display_name": "Marketing / Advertising Enquiry",
                              "forward_to": "shilpa@durian.in", "cc": ["mkt@durian.in"],
                              "team": "Marketing /Advertising"},
    "collaboration_request": {"display_name": "Collaboration Request",
                              "forward_to": "snehal@durian.in", "cc": [],
                              "team": "Collaboration Request"},
    "career_job_enquiry": {"display_name": "Career / Job Enquiry",
                           "forward_to": "hrcoordinator@durian.in", "cc": ["hr@durian.in"]},
}}


class _PFFakeClassifier:
    @staticmethod
    def get_routing_rules(force=False):
        return _PF_RULES


def _pf_setup(monkeypatch, enabled=True):
    import os
    import partnership_forward as pf
    os.environ.pop("LOCAL_FORWARD_OVERRIDE", None)   # .env sets it locally; prod unset
    fake = _PFFakeChatwoot()
    monkeypatch.setattr(pf, "chatwoot", fake)
    monkeypatch.setattr(pf, "classifier", _PFFakeClassifier())
    monkeypatch.setattr(pf.config, "PARTNERSHIP_FORWARD_ENABLED", enabled)
    monkeypatch.setattr(pf.config, "PARTNERSHIP_FORWARD_INBOX_ID", 7)
    monkeypatch.setattr(pf.config, "CHATWOOT_PUBLIC_URL", "https://orm.durianos.in")
    monkeypatch.setattr(pf.config, "CHATWOOT_ACCOUNT_ID", 1)
    return pf, fake


def test_partnership_build_pdf_is_pdf():
    import partnership_forward as pf
    conv = {"meta": {"sender": {"name": "Riya Sharma"}}}
    data = pf.build_pdf(conv, "instagram", "Collaboration Request",
                        [{"message_type": 0, "content": "collab?", "created_at": 1_700_000_000}])
    assert isinstance(data, (bytes, bytearray)) and data[:4] == b"%PDF"


def test_partnership_run_forwards_and_assigns_team(monkeypatch):
    import asyncio
    pf, fake = _pf_setup(monkeypatch)
    conv = {"custom_attributes": {}, "meta": {"sender": {"name": "Riya"}}}
    res = asyncio.run(pf.run(55, conv, "collaboration_request", "instagram"))
    assert res["forwarded"] and res["team"] == "Collaboration Request"
    # emailed the category recipient with a PDF; cc empty here
    assert fake.emails and fake.emails[0][0] == "snehal@durian.in"
    assert fake.emails[0][1].endswith(".pdf") and fake.emails[0][3] is True
    # the ORM link is in the body
    assert "orm.durianos.in/app/accounts/1/conversations/55" in fake.emails[0][4]
    assert fake.teams == [10]                       # Collaboration Request team id
    assert fake.merged and fake.merged[0]["partnership_forwarded"] == "collaboration_request"
    assert fake.notes


def test_partnership_run_marketing_cc_and_team(monkeypatch):
    import asyncio
    pf, fake = _pf_setup(monkeypatch)
    conv = {"custom_attributes": {}, "meta": {"sender": {"name": "Agency X"}}}
    res = asyncio.run(pf.run(60, conv, "marketing_advertising", "facebook"))
    assert res["forwarded"] and fake.emails[0][0] == "shilpa@durian.in"
    assert fake.emails[0][2] == "mkt@durian.in"     # cc joined
    assert fake.teams == [3]


def test_partnership_run_idempotent_disabled_and_bad_category(monkeypatch):
    import asyncio
    pf, fake = _pf_setup(monkeypatch)
    # already forwarded → nothing
    assert asyncio.run(pf.run(1, {"custom_attributes": {"partnership_forwarded": "x"}},
                              "collaboration_request", "instagram"))["reason"] == "already_forwarded"
    assert not fake.emails
    # not a partnership category
    assert asyncio.run(pf.run(1, {"custom_attributes": {}}, "complaint",
                              "instagram"))["reason"] == "category_not_forwardable"
    # disabled flag
    pf2, fake2 = _pf_setup(monkeypatch, enabled=False)
    assert asyncio.run(pf2.run(1, {"custom_attributes": {}}, "collaboration_request",
                               "instagram"))["reason"] == "disabled"
    assert not fake2.emails


def test_job_enquiry_is_emailed_to_hr_with_details(monkeypatch):
    """IG/FB/WhatsApp job seekers reach HR by email (convs 9847 / 10941 were
    only assigned an ORM team), and the candidate is told where to send a CV."""
    import asyncio
    import social_agent as sa
    pf, fake = _pf_setup(monkeypatch, enabled=False)        # partnership flag off …
    monkeypatch.setattr(pf.config, "JOB_HR_FORWARD_ENABLED", True)   # … job forward on
    conv = {"custom_attributes": {"partnership_forwarded": "collaboration_request"},
            "meta": {"sender": {"name": "Nidhi"}}}
    res = asyncio.run(pf.run(77, conv, pf.JOB_CATEGORY, "whatsapp",
                             details="showroom sales, 3 yrs, Pune · 9876543210"))
    assert res["forwarded"] and res["recipient"] == "hrcoordinator@durian.in"
    to, _fname, cc, _pdf, body = fake.emails[0][:5]
    assert to == "hrcoordinator@durian.in" and cc == "hr@durian.in"
    assert "Details shared: showroom sales, 3 yrs, Pune · 9876543210" in body
    # once per job enquiry
    conv["custom_attributes"]["partnership_forwarded"] = pf.JOB_CATEGORY
    assert asyncio.run(pf.run(77, conv, pf.JOB_CATEGORY, "whatsapp"))["reason"] == "already_forwarded"
    monkeypatch.setattr(pf.config, "JOB_HR_FORWARD_ENABLED", False)
    assert asyncio.run(pf.run(78, {"custom_attributes": {}}, pf.JOB_CATEGORY,
                              "instagram"))["reason"] == "disabled"
    line = sa._job_hr_line("hrcoordinator@durian.in")
    assert "HR team" in line and "hrcoordinator@durian.in" in line


def test_partnership_skill_sets_escalate_and_keep_team(monkeypatch):
    import asyncio
    async def fake_run(conv_id, conv, category, channel):
        return {"forwarded": True, "team": "Marketing /Advertising",
                "display": "Marketing / Advertising Enquiry", "recipient": "shilpa@durian.in"}
    monkeypatch.setattr(sa.partnership_forward, "run", fake_run)
    ctx = {"conv_id": 5, "conv": {}, "channel": "instagram"}
    res = asyncio.run(sa._sk_forward_partnership(ctx, category="marketing_advertising",
                                                 customer_message="Shared with our team!"))
    assert res["forwarded"] and ctx["keep_team"] is True
    assert ctx["escalate"]["customer_message"] == "Shared with our team!"
    assert "Marketing /Advertising" in ctx["escalate"]["reason"]


def test_partnership_skill_hidden_until_enabled(monkeypatch):
    monkeypatch.setattr(sa.config, "PARTNERSHIP_FORWARD_ENABLED", False)
    names_off = {t["name"] for t in sa.tools_for_responses("dm")}
    assert "forward_partnership_enquiry" not in names_off
    monkeypatch.setattr(sa.config, "PARTNERSHIP_FORWARD_ENABLED", True)
    names_on = {t["name"] for t in sa.tools_for_responses("dm")}
    assert "forward_partnership_enquiry" in names_on


def test_norm_team_matches_whitespace_and_case():
    import chatwoot
    assert chatwoot._norm_team("Marketing /Advertising") == chatwoot._norm_team("marketing  /advertising ")



# --- Offers scoped by vertical -------------------------------------------------

def _offer(caption, verticals, tags=None):
    return {"active": True, "image_url": "http://x/img.png", "caption": caption,
            "verticals": verticals, "tags": tags or [], "link": ""}


def test_offer_in_vertical():
    assert sa._offer_in_vertical(_offer("x", []), "fhc")             # empty = universal
    assert sa._offer_in_vertical(_offer("x", ["fhc"]), "fhc")
    assert not sa._offer_in_vertical(_offer("x", ["furniture"]), "fhc")
    assert sa._offer_in_vertical(_offer("x", ["Furniture"]), "furniture")   # case-insensitive
    assert sa._offer_in_vertical(_offer("x", ["", "fhc"]), "fhc")    # blank entries ignored


class _OfferFakeChatwoot:
    def __init__(self, offers):
        self._offers = offers
        self.sent = []

    async def get_offers(self):
        return self._offers

    async def send_offer_message(self, conv_id, caption, image_url, link=""):
        self.sent.append(caption)
        return {"id": 1}

    async def merge_custom_attributes(self, conv_id, attrs):
        pass


def test_share_offer_scopes_to_vertical(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa.config, "OFFERS_ENABLED", True)
    offers = [_offer("Furniture sale", ["furniture"]),
              _offer("FHC studio offer", ["fhc"]),
              _offer("Universal offer", [])]

    # FHC chat → never the furniture-only offer (gets the fhc or universal one)
    fake = _OfferFakeChatwoot(offers)
    monkeypatch.setattr(sa, "chatwoot", fake)
    ctx = {"conv": {"custom_attributes": {}}, "conv_id": 1, "vertical": "fhc", "profile": {}}
    res = asyncio.run(sa._sk_share_offer(ctx))
    assert res["sent"] and "Furniture sale" not in fake.sent[0]

    # Furniture chat → the furniture offer is eligible again
    fake2 = _OfferFakeChatwoot(offers)
    monkeypatch.setattr(sa, "chatwoot", fake2)
    ctx2 = {"conv": {"custom_attributes": {}}, "conv_id": 2, "vertical": "furniture", "profile": {}}
    asyncio.run(sa._sk_share_offer(ctx2))
    assert fake2.sent and fake2.sent[0] == "Furniture sale"

    # A vertical with no matching/universal offer → nothing sent
    fake3 = _OfferFakeChatwoot([_offer("Furniture sale", ["furniture"])])
    monkeypatch.setattr(sa, "chatwoot", fake3)
    ctx3 = {"conv": {"custom_attributes": {}}, "conv_id": 3, "vertical": "fhc", "profile": {}}
    res3 = asyncio.run(sa._sk_share_offer(ctx3))
    assert not res3["sent"] and not fake3.sent


# --- search_products: a NAMED product is searched by name (conv 10241) ---------
# The agent searched "recliner" for "glass table and Quinn recliner", Quinn was
# not in that top 6, and it told the customer Quinn didn't exist.

_QUINN_MSG = ("I had sent a text on insta for a glass table and Quinn recliner... "
              "can I know the price")
_QUINN = {"title": "Quinn", "category": "Reclining Sofas", "selling_price": 51840,
          "mrp": 115200, "url": "https://www.durian.in/product/quinn"}
_VIVIAN = {"title": "Vivian", "category": "Reclining Sofas", "selling_price": 108000,
           "mrp": 216000, "url": "https://www.durian.in/product/vivian"}
_JESSICA = {"title": "Jessica", "category": "Coffee & Center Tables",
            "selling_price": 15120, "mrp": 43200,
            "url": "https://www.durian.in/product/jessica"}


def _named_search(monkeypatch, results):
    import website_search as ws
    calls = []

    async def fake(query, rows=4, sort="", min_price=None, max_price=None):
        calls.append(query)
        return list(results.get(query.lower(), []))
    monkeypatch.setattr(ws, "search", fake)
    return calls


class _LabelChatwoot:
    def __init__(self):
        self.labels = []

    async def add_label(self, cid, label):
        self.labels.append((cid, label))


def test_family_matches_by_kind():
    import product_catalog as pc
    assert pc.family_matches("QUINN", "recliner")
    assert pc.family_matches("QUINN", "recliners")
    assert pc.family_matches("QUINN", "sofa")
    assert not pc.family_matches("QUINN", "glass table")


def test_search_products_adds_dropped_name(monkeypatch):
    import asyncio
    calls = _named_search(monkeypatch, {"recliner": [_VIVIAN], "quinn": [_QUINN]})
    handler = sa.SKILLS["search_products"]["handler"]
    ctx = {"incoming_all": ["Hey", _QUINN_MSG]}
    out = asyncio.run(handler(ctx, query="recliner"))
    assert calls == ["recliner", "Quinn"]
    assert [p["title"] for p in out["products"]] == ["Quinn", "Vivian"]
    assert out["products"][0]["price"] == "₹51,840"
    assert "note" not in out
    # looked up once per turn, not on every later search
    asyncio.run(handler(ctx, query="recliner"))
    assert calls.count("Quinn") == 1


def test_search_products_reminds_when_query_is_other_kind(monkeypatch):
    import asyncio
    calls = _named_search(monkeypatch, {"glass table": [_JESSICA]})
    handler = sa.SKILLS["search_products"]["handler"]
    out = asyncio.run(handler({"incoming_all": [_QUINN_MSG]}, query="glass table"))
    assert calls == ["glass table"]
    assert [p["title"] for p in out["products"]] == ["Jessica"]
    assert "also named Quinn" in out["note"]


def test_search_products_named_not_listed_captures_lead(monkeypatch):
    import asyncio
    _named_search(monkeypatch, {"recliner": [_VIVIAN]})
    cw = _LabelChatwoot()
    monkeypatch.setattr(sa, "chatwoot", cw)
    handler = sa.SKILLS["search_products"]["handler"]
    out = asyncio.run(handler({"incoming_all": [_QUINN_MSG], "conv_id": 7},
                              query="recliner"))
    assert [p["title"] for p in out["products"]] == ["Vivian"]
    assert "not listed online" in out["note"]
    assert "do NOT say we don't have it" in out["note"]
    assert cw.labels == [(7, "product-check")]


def test_search_products_name_in_query_but_unlisted(monkeypatch):
    import asyncio
    calls = _named_search(monkeypatch, {})
    cw = _LabelChatwoot()
    monkeypatch.setattr(sa, "chatwoot", cw)
    handler = sa.SKILLS["search_products"]["handler"]
    out = asyncio.run(handler({"incoming_all": [_QUINN_MSG], "conv_id": 7},
                              query="Quinn recliner"))
    assert calls == ["Quinn recliner", "Quinn"]
    assert out["products"] == [] and "not listed online" in out["note"]
    assert "try different words" not in out["note"]


def test_search_products_generic_ask_unchanged(monkeypatch):
    import asyncio
    calls = _named_search(monkeypatch, {"sofa": [_VIVIAN]})
    handler = sa.SKILLS["search_products"]["handler"]
    out = asyncio.run(handler({"incoming_all": ["hi do you have a 3 seater sofa in grey"]},
                              query="sofa"))
    assert calls == ["sofa"]
    assert [p["title"] for p in out["products"]] == ["Vivian"] and "note" not in out


# --- Comment + B2B handoff fixes (convs 9978, 9979, 9996, 10009, 10011, 10110) ---

def test_comment_toolset_uses_narrow_escalate_description():
    comment = {t["name"]: t for t in sa.tools_for_responses("comment")}
    assert set(comment) == {"escalate_to_human", "finish"}
    desc = comment["escalate_to_human"]["description"]
    assert "NEVER for a price or product" in desc and "store / location" in desc
    assert "written for you" in desc and "ignore" in desc
    assert "price negotiation" not in desc             # the DM trigger list is gone
    dm = {t["name"]: t for t in sa.tools_for_responses("")}
    assert "price negotiation" in dm["escalate_to_human"]["description"]


def test_comment_prompt_covers_location_objection_offtopic():
    from datetime import datetime
    p = sa._comment_prompt("durianfurniture_official", "furniture", datetime(2026, 10, 19),
                           "P", "T", post_caption="Franceska dining table")
    for case in ("STORE / LOCATION ask", "PRICE / VALUE OBJECTION", "OFF-TOPIC / UNRELATED"):
        assert case in p
    assert "never says a request was escalated, forwarded, reviewed" in p
    assert "NEVER for a price, location, product" in p


def test_dm_prompt_captures_business_leads_before_handoff():
    from datetime import datetime
    p = sa._system_prompt(surface="dm", inbox="durianforhome", vertical="furniture",
                          now=datetime(2026, 10, 19), profile_block="P", templates="T",
                          n_customer_msgs=1)
    assert "BUSINESS LEADS" in p and "CAPTURE it before any handoff" in p
    assert "line=furniture" in p
    assert "dealer / \\\nfranchise; bulk / B2B / projects" not in p
    esc = sa.SKILLS["escalate_to_human"]["description"]
    assert "ONLY after register_enquiry" in esc and "category" in esc


def test_comment_violation_blocks_escalation_and_refund_talk():
    bad = ["We've escalated your refund request to our team.",
           "We will process your refund soon.", "You'll get your money back."]
    for reply in bad:
        assert "escalation / refund" in sa.comment_violation(reply), reply
    for ok in (sa._COMMENT_HANDOFF_LINE,
               "We'd love to help — please DM us to know more about our stores near you.",
               "We hear you! Please DM us so we can suggest options that suit your budget."):
        assert sa.comment_violation(ok) == "", ok


def test_handoff_pending_needs_label_and_stamp():
    stamp = {sa._HANDOFF_SENT_ATTR: "2026-10-15T12:40:00+05:30"}
    assert sa._handoff_pending({"labels": ["agent-needed"], "custom_attributes": stamp})
    assert not sa._handoff_pending({"labels": [], "custom_attributes": stamp})   # resolved
    assert not sa._handoff_pending({"labels": ["agent-needed"], "custom_attributes": {}})


# Full agent turns with a scripted model (no live LLM).

class _TurnChatwoot:
    def __init__(self, msgs=None, inbox_name="durianfurniture_official", teams=None):
        self.msgs = msgs or []
        self.public, self.private, self.labels, self.attrs = [], [], [], {}
        self.inbox_name, self.teams, self.team = inbox_name, teams or {}, None

    async def get_inbox_name(self, inbox_id):
        return self.inbox_name

    async def find_team_id(self, name):
        return self.teams.get(name)

    async def get_conversation_messages_raw(self, conv_id):
        return self.msgs

    async def merge_custom_attributes(self, conv_id, attrs):
        self.attrs.update(attrs)

    async def add_label(self, conv_id, label):
        self.labels.append(label)

    async def create_message(self, conv_id, content, message_type="outgoing",
                             private=False, content_attributes=None):
        (self.private if private else self.public).append(
            {"content": content, "attrs": content_attributes or {}})
        return {"id": 1}

    async def post_private_note(self, conv_id, content):
        self.private.append({"content": content, "attrs": {}})

    async def assign_team(self, conv_id, team_id):
        self.team = team_id


def _scripted_turn(monkeypatch, calls, *, surface="comment", latest="Price",
                   conv_extra=None, channel="instagram",
                   inbox_name="durianfurniture_official", teams=None):
    """Run one real _handle_locked turn where the model makes `calls`
    ([(tool_name, args), ...]) in its first response."""
    import asyncio
    import json as _json
    from types import SimpleNamespace as NS
    cw = _TurnChatwoot([{"id": 7, "message_type": 0, "content": latest, "created_at": 0}],
                       inbox_name=inbox_name, teams=teams)
    monkeypatch.setattr(sa, "chatwoot", cw)
    monkeypatch.setattr(sa.config, "SOCIAL_AUTO_SEND_ENABLED", True)
    monkeypatch.setattr(sa.config, "SOCIAL_AGENT_HANDOFF_TEAM_ID", 0)

    async def _none(*a, **k):
        return None

    async def _empty(*a, **k):
        return ""
    for name in ("load", "soft_link", "crm_lookup", "save"):
        monkeypatch.setattr(sa.profile_mod, name, _none)
    monkeypatch.setattr(sa.profile_mod, "prior_transcript", _empty)
    monkeypatch.setattr(sa, "_templates_block", _empty)
    responses = [NS(output=[NS(type="function_call", name=n, arguments=_json.dumps(a),
                               call_id=f"c{i}") for i, (n, a) in enumerate(calls)],
                    output_text="")]

    async def fake_llm(**kw):
        return responses.pop(0) if responses else NS(output=[], output_text="")
    monkeypatch.setattr(sa, "_responses_with_retry", fake_llm)
    sa._last_handled_msgid.pop(77, None)
    conv = {"id": 77, "inbox_id": 16, "labels": [],
            "meta": {"sender": {"id": 5, "name": "Fatima"}, "channel": "Instagram"},
            "custom_attributes": {}, "additional_attributes": {}}
    conv.update(conv_extra or {})
    res = asyncio.run(sa._handle_locked(conv, 77, channel, surface, latest, 7))
    return res, cw


def test_comment_silent_escalation_now_sends_dm_invite(monkeypatch):
    # convs 10009 / 10011: "Price" handed over with no customer_message → silence
    res, cw = _scripted_turn(monkeypatch, [("escalate_to_human", {"reason": "price ask"})])
    assert res["handled"] == "agent_escalated"
    assert [m["content"] for m in cw.public] == [sa._COMMENT_HANDOFF_LINE]
    assert "agent-needed" in cw.labels                    # still flagged for a look
    assert not any("without replying" in m["content"] for m in cw.private)


def test_comment_refund_promise_replaced_by_dm_invite(monkeypatch):
    # conv 9979: "We've escalated your refund request" must never go public
    res, cw = _scripted_turn(monkeypatch, [("escalate_to_human", {
        "reason": "refund", "customer_message":
            "We've escalated your refund request to our team."})],
        latest="RETURN ALL MY MONEY")
    assert [m["content"] for m in cw.public] == [sa._COMMENT_HANDOFF_LINE]


def test_comment_no_reply_still_gets_dm_invite(monkeypatch):
    res, cw = _scripted_turn(monkeypatch, [])       # model says nothing at all
    assert res["handled"] == "agent_no_reply_dm_invite"
    assert [m["content"] for m in cw.public] == [sa._COMMENT_HANDOFF_LINE]


def test_dm_handoff_line_sent_once_then_not_repeated(monkeypatch):
    # conv 10110: "we're forwarding your enquiry" went out on five turns
    esc = [("escalate_to_human", {"reason": "B2B lead: Kanpur showroom supply",
                                  "customer_message": "Thank you — our team will be in touch."})]
    _, first = _scripted_turn(monkeypatch, esc, surface="", latest="yes")
    assert [m["content"] for m in first.public] == ["Thank you — our team will be in touch."]
    assert sa._HANDOFF_SENT_ATTR in first.attrs
    _, again = _scripted_turn(monkeypatch, esc, surface="", latest="ok please do",
                              conv_extra={"labels": ["agent-needed"],
                                          "custom_attributes": dict(first.attrs)})
    assert again.public == []                              # not re-announced
    note = [m for m in again.private if "without replying" in m["content"]]
    assert note and note[0]["attrs"]["ai_trace"]           # reason recorded, not lost
    assert any(s.get("label") == "Handoff already pending"
               for s in note[0]["attrs"]["ai_trace"])


def test_dm_silent_no_reply_leaves_reasoning_note(monkeypatch):
    res, cw = _scripted_turn(monkeypatch, [], surface="", latest="hmm")
    assert res["handled"] == "agent_no_reply" and cw.public == []
    assert any("without replying" in m["content"] and m["attrs"].get("ai_trace")
               for m in cw.private)


# --- Honest, routed hand-overs (convs 10472, 10460, 10507, 10505) -------------

_TEAMS = {"complaint": 4, "career/job enquiry": 1, "legal complaint": 2,
          "full home customization  (enquiry & complaints)": 13,
          "project/bulk order": 12, "existing order enquiry": 8}


class _AttrChatwoot:
    def __init__(self):
        self.attrs = {}

    async def merge_custom_attributes(self, cid, attrs):
        self.attrs.update(attrs)


def _esc_ctx(surface="", incoming=None, phone=None, ca=None):
    prof = {"identity": {"phone": {"value": phone}}} if phone else {}
    return {"conv": {"custom_attributes": dict(ca or {})}, "conv_id": 3,
            "surface": surface, "profile": prof, "incoming_all": incoming or [],
            "escalate": None}


def test_complaint_handoff_asks_for_details_once(monkeypatch):
    import asyncio
    cw = _AttrChatwoot()
    monkeypatch.setattr(sa, "chatwoot", cw)
    h = sa.SKILLS["escalate_to_human"]["handler"]
    ctx = _esc_ctx(incoming=["One of the worst post purchase service"])
    r = asyncio.run(h(ctx, category="complaint", reason="post-purchase service"))
    assert r["escalated"] is False and ctx["escalate"] is None
    assert "their contact number" in r["missing"] and len(r["missing"]) == 2
    assert "Do NOT say anything was shared" in r["note"]
    assert cw.attrs[sa._DETAILS_ASKED_ATTR] == "complaint"
    # their next message (a new turn): asked once already → a customer who
    # won't share still gets handed over
    nxt = _esc_ctx(incoming=["just call me back"], ca=ctx["conv"]["custom_attributes"])
    r2 = asyncio.run(h(nxt, category="complaint", reason="post-purchase service"))
    assert r2["escalated"] is True and nxt["escalate"]["category"] == "complaint"


def test_complaint_handoff_passes_with_phone_and_details(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _AttrChatwoot())
    h = sa.SKILLS["escalate_to_human"]["handler"]
    ctx = _esc_ctx(incoming=["sofa leg broke, order 45812 from Pune. 9876543210"])
    r = asyncio.run(h(ctx, category="complaint", reason="broken sofa",
                      details="Benjamin sofa leg broke, order 45812, Pune"))
    assert r["escalated"] is True


def test_whatsapp_job_handoff_uses_known_number(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _AttrChatwoot())
    h = sa.SKILLS["escalate_to_human"]["handler"]
    ctx = _esc_ctx(phone="9999923147")
    r = asyncio.run(h(ctx, category="job", reason="job seeker",
                      details="site supervisor, 6 years, Bhopal"))
    assert r["escalated"] is True


def test_business_lead_handoff_needs_registration(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _AttrChatwoot())
    h = sa.SKILLS["escalate_to_human"]["handler"]
    r = asyncio.run(h(_esc_ctx(), category="bulk", reason="wholesale"))
    assert r["escalated"] is False and "register_enquiry" in r["note"]
    r2 = asyncio.run(h(_esc_ctx(ca={"crm_deal_id": "D1"}), category="bulk", reason="wholesale"))
    assert r2["escalated"] is True


def test_comment_handoff_is_never_gated(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _AttrChatwoot())
    h = sa.SKILLS["escalate_to_human"]["handler"]
    r = asyncio.run(h(_esc_ctx(surface="comment"), category="complaint", reason="x"))
    assert r["escalated"] is True


def test_unbacked_claims_are_caught():
    for claim in ["We've shared your complaint with our customer support team.",
                  "A teammate will review this conversation and assist further.",
                  "Our team will review this and get back to you shortly.",
                  "We've shared your request with the concerned team, who will get back to you here.",
                  "We're forwarding your wholesale enquiry to our team for assistance."]:
        assert sa.unbacked_claim(claim), claim
        assert sa.comment_violation(claim), claim
    for fine in ["Please share your contact number and city.",
                 sa._COMMENT_HANDOFF_LINE, sa._COMMENT_COMPLAINT_LINE,
                 "We'd love to help — please DM us to know more about our stores near you."]:
        assert not sa.unbacked_claim(fine), fine


def test_dm_reply_with_claim_is_held_unless_routed():
    claim = "We've shared your complaint with our customer support team."
    hold, _ = sa.hold_reason(claim, surface="", prof={}, sources=claim)
    assert "nothing was routed" in hold
    for kw in ({"routed": True}, {"wrote_this_turn": True}):
        hold, _ = sa.hold_reason(claim, surface="", prof={}, sources=claim, **kw)
        assert "nothing was routed" not in hold


def test_other_handoff_line_drops_model_claims():
    assert sa._handoff_line("other", "", False, "We've shared your request with the team.") \
        == sa._DM_OTHER_LINE
    assert sa._handoff_line("other", "", False, "Thank you, one moment.") == "Thank you, one moment."
    assert sa._handoff_line("job", "", True).startswith("Thank you for your interest in working")


def _esc_call(category, **extra):
    return ("escalate_to_human", {"category": category, "reason": category, **extra})


def test_turn_dm_complaint_routes_to_support_team(monkeypatch):
    # conv 10472 done right: details in hand → routed to the complaint team, and
    # only THEN told it was passed on
    res, cw = _scripted_turn(
        monkeypatch, [_esc_call("complaint", details="dining table top cracked, invoice 7781, Noida",
                                phone="9876543210")],
        surface="", latest="invoice 7781, table top cracked, 9876543210",
        inbox_name="durianforhome", teams=_TEAMS)
    assert res["team"] == "complaint" and cw.team == 4
    assert [m["content"] for m in cw.public] == [sa._DM_HANDOFF_LINES["complaint"][1]]


def test_turn_whatsapp_job_goes_to_career_team(monkeypatch):
    # conv 10505: "Carpenter or site supervisor required ?"
    res, cw = _scripted_turn(
        monkeypatch, [_esc_call("job", details="site supervisor, 5 years, Indore")],
        surface="", latest="Carpenter or site supervisor required ?", channel="whatsapp",
        inbox_name="Whatsapp FHC", teams=_TEAMS,
        conv_extra={"meta": {"sender": {"id": 5, "name": "Aslam",
                                        "phone_number": "+919999923147"},
                             "channel": "Whatsapp"}})
    assert res["team"] == "career/job enquiry" and cw.team == 1
    sent = cw.public[0]["content"]
    assert "working with Durian" in sent and "shared your request" not in sent


def test_turn_fhc_complaint_routes_to_fhc_team(monkeypatch):
    res, cw = _scripted_turn(
        monkeypatch, [_esc_call("complaint", details="kitchen shutter misaligned, Gurgaon",
                                phone="9876543210")],
        surface="", latest="kitchen shutter misaligned 9876543210",
        inbox_name="durian_fullhomecustomisation", teams=_TEAMS)
    assert cw.team == 13


def test_turn_unrouted_complaint_makes_no_forward_claim(monkeypatch):
    res, cw = _scripted_turn(
        monkeypatch, [_esc_call("complaint", details="sofa fabric torn, order 551",
                                phone="9876543210")],
        surface="", latest="sofa fabric torn 9876543210", inbox_name="durianforhome",
        teams={})                                          # team missing in Chatwoot
    assert res["team"] == "" and cw.team is None
    assert [m["content"] for m in cw.public] == [sa._DM_HANDOFF_LINES["complaint"][0]]
    assert not sa.unbacked_claim(cw.public[0]["content"])


def test_turn_comment_complaint_invites_dm(monkeypatch):
    # conv 10507: "Poor quality and worst in service"
    res, cw = _scripted_turn(monkeypatch, [_esc_call("complaint")],
                             latest="Poor quality and worst in service", teams=_TEAMS)
    assert [m["content"] for m in cw.public] == [sa._COMMENT_COMPLAINT_LINE]
    assert cw.team == 4 and "agent-needed" in cw.labels


def _ignore_call():
    return ("finish", {"action": "ignore", "reply": "", "confidence": 92,
                       "reasoning": "pure abuse, nothing about Durian",
                       "profile_updates": []})


def test_turn_pure_abuse_is_ignored(monkeypatch):
    # conv 10460: no reply, no hand-over, but an audit note with the reasoning
    res, cw = _scripted_turn(monkeypatch, [_ignore_call()],
                             latest="Tum apni bahan chudwa rahe ho")
    assert res == {"handled": "agent_ignored"}
    assert cw.public == [] and "agent-needed" not in cw.labels
    assert any("ignored on purpose" in m["content"] and m["attrs"].get("ai_trace")
               for m in cw.private)


def test_turn_ignore_refused_when_message_has_an_issue(monkeypatch):
    res, cw = _scripted_turn(monkeypatch, [_ignore_call()],
                             latest="you idiots, my sofa delivery is 3 weeks late")
    assert res == {"handled": "agent_ignore_refused"}
    assert cw.public == [] and "agent-needed" in cw.labels


def test_details_gate_holds_for_a_same_turn_retry(monkeypatch):
    import asyncio
    monkeypatch.setattr(sa, "chatwoot", _AttrChatwoot())
    h = sa.SKILLS["escalate_to_human"]["handler"]
    ctx = _esc_ctx(incoming=["worst service ever"])
    assert asyncio.run(h(ctx, category="complaint", reason="x"))["escalated"] is False
    # the model calls again in the SAME turn instead of asking → still refused
    assert asyncio.run(h(ctx, category="complaint", reason="x"))["escalated"] is False
    # next turn (fresh ctx, attr persisted) → the customer was asked → goes through
    nxt = _esc_ctx(incoming=["worst service ever"], ca=ctx["conv"]["custom_attributes"])
    assert asyncio.run(h(nxt, category="complaint", reason="x"))["escalated"] is True


# --- Settings → Product Catalog: import, live list, sync, showroom prices -------

def _xlsx(sheets: dict) -> bytes:
    """{tab: [rows]} → .xlsx bytes."""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _temp_catalog_store(monkeypatch):
    import os
    import tempfile
    import config_store
    import product_catalog
    monkeypatch.setattr(config_store, "_DB_PATH",
                        os.path.join(tempfile.mkdtemp(), "config_store.db"))
    config_store.init()
    product_catalog.reload()


_TEMPLATE_CSV = ("SKU,Name,MRP,Sale Price,Category\n"
                 "QUINN/1,1STR LEATHER MOTORIZED RECLINER,\"1,15,200\",\"₹69,120\",RECLINING SOFAS\n"
                 "QUINN/2,2STR LEATHER MOTORIZED RECLINER,193000,115800,RECLINING SOFAS\n"
                 ",missing sku,100,90,X\n"
                 "QUINN/2,2STR LEATHER MOTORIZED RECLINER,193000,99000,RECLINING SOFAS\n")


def test_catalog_import_simple_csv():
    import catalog_import as ci
    res = ci.parse_file(_TEMPLATE_CSV.encode(), "prices.csv")
    assert res["format"] == "template"
    p = res["products"]
    assert p["QUINN/1"] == {"name": "1STR LEATHER MOTORIZED RECLINER", "family": "QUINN",
                            "mrp": 115200.0, "sale_price": 69120,
                            "category": "RECLINING SOFAS", "source": "sheet"}
    assert p["QUINN/2"]["sale_price"] == 99000          # duplicate: last row wins
    joined = " ".join(res["warnings"])
    assert "1 rows skipped" in joined and "duplicate" in joined


def test_catalog_import_template_xlsx_header_not_first_row():
    import catalog_import as ci
    data = _xlsx({"Prices": [("Durian price list Oct",),
                             ("Code", "Product Name", "MRP", "Selling Price", "Category"),
                             ("MATILDA/1", "1STR RECLINER", 76400, 38200, "RECLINING SOFAS")]})
    res = ci.parse_file(data, "prices.xlsx")
    assert res["products"]["MATILDA/1"]["sale_price"] == 38200


def test_catalog_import_monthly_workbook():
    import catalog_import as ci
    durian = [("title",), ("SKU ID", "MRP", "BP", "Offer", None, None, None, None, "CATEGORY"),
              ("RIHANNA/A/2", 100000, 0, 52000, None, None, None, None, "SOFA"),
              ("NOPRICE/1", 5000, 0, None, None, None, None, None, "X")]
    rt = [("title",), ("No.", "Description", None, "MRP", None, None, None, "BP", None, "Category"),
          ("RIHANNA/A/2", "2STR SOFA", None, 100000, None, None, None, 52000, None, "SOFA"),
          ("ONLYRT/1", "SIDE TABLE", None, 9000, None, None, None, 4500, None, "TABLES")]
    res = ci.parse_file(_xlsx({"Durian": durian, "RT": rt}), "REVISED SALE PRICE.xlsx")
    assert res["format"] == "workbook"
    p = res["products"]
    assert p["RIHANNA/A/2"]["sale_price"] == 52000 and p["RIHANNA/A/2"]["name"] == "2STR SOFA"
    assert p["ONLYRT/1"]["source"] == "rt" and p["ONLYRT/1"]["sale_price"] == 4500
    assert "NOPRICE/1" not in p and "1 SKUs skipped" in " ".join(res["warnings"])


def test_catalog_import_rejects_wrong_files():
    import catalog_import as ci
    for data, name, msg in [(b"", "x.csv", "empty"),
                            (b"Item,Qty\nA,1\n", "x.csv", "header row"),
                            (b"not a workbook", "x.xlsx", "Couldn't read"),
                            (("SKU,Sale price\n" + "A,\n" * 5 + "B,10\n").encode(),
                             "x.csv", "rows are missing")]:
        try:
            ci.parse_file(data, name)
            raise AssertionError(f"accepted {name}")
        except ci.CatalogImportError as e:
            assert msg in str(e), str(e)


def test_catalog_sheet_link_only_google():
    import asyncio
    import catalog_import as ci
    good = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0"
    assert ci.sheet_id(good) == "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    for bad in ("http://169.254.169.254/latest", "https://evil.com/spreadsheets/d/x",
                "https://docs.google.com.evil.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWx"):
        assert ci.sheet_id(bad) is None
        try:
            asyncio.run(ci.fetch_sheet(bad))
            raise AssertionError("fetched a non-Google URL")
        except ci.CatalogImportError as e:
            assert "Google Sheets link" in str(e)


def test_catalog_diff_and_export_roundtrip():
    import catalog_import as ci
    old = ci.parse_file(_TEMPLATE_CSV.encode(), "a.csv")["products"]
    new = {**old, "QUINN/1": {**old["QUINN/1"], "sale_price": 65000},
           "QUINN/2": {**old["QUINN/2"], "category": "RECLINERS"},
           "NEW/1": {"name": "NEW", "family": "NEW", "mrp": None, "sale_price": 10,
                     "category": "", "source": "sheet"}}
    d = ci.diff(old, new)
    assert (d["added"], d["removed"], d["price_changed"], d["details_changed"]) == (1, 0, 1, 1)
    assert d["changed_sample"][0] == {"sku": "QUINN/1", "name": "1STR LEATHER MOTORIZED RECLINER",
                                      "old": 69120, "new": 65000}
    back = ci.parse_file(ci.export_csv(new).encode(), "export.csv")["products"]
    assert {k: v["sale_price"] for k, v in back.items()} == {k: v["sale_price"] for k, v in new.items()}
    assert back["QUINN/2"]["category"] == "RECLINERS" and back["NEW/1"]["family"] == "NEW"


def test_product_catalog_serves_published_list(monkeypatch):
    import catalog_import as ci
    import catalog_sync
    import config_store
    import product_catalog as pc
    _temp_catalog_store(monkeypatch)
    try:
        floor_count = pc.meta()["count"]
        assert pc.meta()["managed"] is False and floor_count > 1000
        assert not pc.is_fresh(45)
        doc = ci.build_doc(ci.parse_file(_TEMPLATE_CSV.encode(), "oct.csv"),
                           "upload", "oct.csv", "1–31 Oct 2026")
        vid = catalog_sync.publish_catalog(doc, "Upload: oct.csv", "admin@durian.in")
        m = pc.meta()
        assert m["managed"] and m["version_id"] == vid and m["count"] == 2
        assert m["price_period"] == "1–31 Oct 2026" and m["published_by"] == "admin@durian.in"
        assert pc.is_fresh(45)
        assert [h["sku"] for h in pc.search("quinn")] == ["QUINN/1", "QUINN/2"]
        # a broken published doc never takes the agent down — the floor serves
        config_store.publish({"products": {}}, domain=config_store.CATALOG)
        pc.reload()
        assert pc.meta()["managed"] is False and pc.meta()["count"] == floor_count
    finally:
        pc.reload()


def test_catalog_sync_publishes_only_on_change(monkeypatch):
    import asyncio
    import catalog_import as ci
    import catalog_sync
    import config_store
    import product_catalog as pc
    _temp_catalog_store(monkeypatch)
    url = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit"
    sheet = {"rows": [("SKU", "Name", "MRP", "Sale price", "Category"),
                      ("QUINN/1", "1STR RECLINER", 115200, 69120, "RECLINING SOFAS")]}

    async def fake_fetch(u):
        assert u == url
        return _xlsx({"Sheet1": sheet["rows"]})
    monkeypatch.setattr(ci, "fetch_sheet", fake_fetch)
    try:
        assert asyncio.run(catalog_sync.sync_once())["error"] == "No Google Sheet is linked."
        catalog_sync.save_settings(url, True, "admin")
        assert catalog_sync.settings() == {"sheet_url": url, "auto_sync": True}
        first = asyncio.run(catalog_sync.sync_once())
        assert first["ok"] and first["changed"] and pc.meta()["source"] == "sheet"
        again = asyncio.run(catalog_sync.sync_once())
        assert again["ok"] and not again["changed"] and "version_id" not in again
        assert len(config_store.list_versions(config_store.CATALOG)) == 1
        sheet["rows"][1] = ("QUINN/1", "1STR RECLINER", 115200, 64999, "RECLINING SOFAS")
        third = asyncio.run(catalog_sync.sync_once())
        assert third["changed"] and third["summary"]["price_changed"] == 1
        assert pc.get("QUINN/1")["sale_price"] == 64999
        assert catalog_sync.last_status["ok"] and catalog_sync.last_status["changed"]
    finally:
        pc.reload()


def test_catalog_admin_api_preview_publish_rollback(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    import catalog_sync
    import main
    import product_catalog as pc
    _temp_catalog_store(monkeypatch)
    monkeypatch.setattr(main.config, "ROUTING_ADMIN_SECRET", "s3cret")
    client = TestClient(main.app)
    h = {"X-Routing-Admin-Secret": "s3cret"}
    try:
        assert client.get("/admin/catalog-config").status_code == 401
        got = client.get("/admin/catalog-config", headers=h).json()
        assert got["meta"]["managed"] is False and got["sync"]["sheet_url"] == ""
        body = {"file_b64": base64.b64encode(_TEMPLATE_CSV.encode()).decode(),
                "filename": "oct.csv"}
        prev = client.post("/admin/catalog-config/preview", json=body, headers=h).json()
        assert prev["format"] == "template" and prev["summary"]["count"] == 2
        assert pc.meta()["managed"] is False            # preview saves nothing
        bad = client.post("/admin/catalog-config/preview", headers=h,
                          json={"file_b64": "%%%", "filename": "x.csv"})
        assert bad.status_code == 422
        # an upload pauses a linked sheet's auto-sync
        catalog_sync.save_settings("https://docs.google.com/spreadsheets/d/"
                                   "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", True, "a")
        pub = client.post("/admin/catalog-config/publish", headers=h,
                          json={**body, "price_period": "Oct 2026", "actor": "a@x"}).json()
        assert pub["ok"] and pub["auto_sync_paused"] is True
        assert catalog_sync.settings()["auto_sync"] is False
        got = client.get("/admin/catalog-config", headers=h).json()
        assert got["meta"]["count"] == 2 and got["fresh"] is True
        assert [r["sku"] for r in got["products"]] == ["QUINN/1", "QUINN/2"]
        ver = client.get(f"/admin/catalog-config/versions/{pub['version_id']}",
                         headers=h).json()
        assert ver["count"] == 2 and ver["price_period"] == "Oct 2026"
        exp = client.get("/admin/catalog-config/export", headers=h).json()
        assert exp["csv"].startswith("SKU,Name,MRP,Sale price,Category,Family")
        # publish a second list, then roll back to the first
        two = _TEMPLATE_CSV.replace("69,120", "60,000").encode()
        client.post("/admin/catalog-config/publish", headers=h,
                    json={"file_b64": base64.b64encode(two).decode(), "filename": "v2.csv"})
        assert pc.get("QUINN/1")["sale_price"] == 60000
        rb = client.post("/admin/catalog-config/rollback", headers=h,
                         json={"version_id": pub["version_id"]})
        assert rb.status_code == 200 and pc.get("QUINN/1")["sale_price"] == 69120
    finally:
        pc.reload()


def test_named_product_quotes_fresh_showroom_price(monkeypatch):
    import asyncio
    import catalog_import as ci
    import catalog_sync
    import config_store
    import product_catalog as pc
    _named_search(monkeypatch, {"recliner": [_VIVIAN]})       # Quinn not online
    monkeypatch.setattr(sa, "chatwoot", _LabelChatwoot())
    _temp_catalog_store(monkeypatch)
    handler = sa.SKILLS["search_products"]["handler"]
    try:
        doc = ci.build_doc(ci.parse_file(_TEMPLATE_CSV.encode(), "oct.csv"), "upload", "oct.csv")
        catalog_sync.publish_catalog(doc, "", "admin")
        out = asyncio.run(handler({"incoming_all": [_QUINN_MSG], "conv_id": 9},
                                  query="recliner"))
        note = out["note"]
        assert "showroom price list" in note
        assert "1-Seater Leather Motorized Recliner — ₹69,120 (MRP ₹1,15,200)" in note
        # a list published long ago is not current enough to quote
        old = {"at": "2026-01-01T00:00:00+00:00"}
        monkeypatch.setattr(config_store, "active_version",
                            lambda domain: {"id": 999, "note": "", "created_by": "",
                                            "created_at": old["at"]})
        monkeypatch.setattr(config_store, "get_active_override",
                            lambda domain: doc if domain == config_store.CATALOG else {})
        pc.reload()
        stale = asyncio.run(handler({"incoming_all": [_QUINN_MSG], "conv_id": 9},
                                    query="recliner"))
        assert "do not quote a price" in stale["note"] and "₹69,120" not in stale["note"]
    finally:
        pc.reload()


# --- WhatsApp campaign leads are told apart in the CRM -------------------------

_AD_CONV = {"custom_attributes": {"lead_source": "whatsapp_campaign", "campaign_ad": {
    "headline": "Trusted By 3.2 Million Customers.",
    "body": "Customised for your home. Delivered by Durian."}},
    "meta": {"channel": "Channel::Whatsapp"}}
_REPLY_CONV = {"custom_attributes": {}, "meta": {"channel": "Channel::Whatsapp"},
               "additional_attributes": {"reply_campaign": {
                   "title": "Grand Festive Offer", "message": "Flat 50% off select furniture"}}}
_PLAIN_CONV = {"custom_attributes": {}, "meta": {"channel": "Channel::Instagram"}}


def test_whatsapp_campaign_detection():
    import main
    ad = main._whatsapp_campaign(_AD_CONV)
    assert ad == {"medium": "click-to-whatsapp ad", "name": "Trusted By 3.2 Million Customers.",
                  "content": "Customised for your home. Delivered by Durian."}
    bare_ad = main._whatsapp_campaign({"custom_attributes": {"lead_source": "whatsapp_campaign"}})
    assert bare_ad["medium"] == "click-to-whatsapp ad" and bare_ad["name"] == ""
    assert main._whatsapp_campaign(_REPLY_CONV)["name"] == "Grand Festive Offer"
    assert main._whatsapp_campaign(_PLAIN_CONV) == {} and main._whatsapp_campaign(None) == {}


def test_campaign_integration_source(monkeypatch):
    import main
    monkeypatch.setattr(main.config, "ZOHO_CRM_CAMPAIGN_INTEGRATION_SOURCE",
                        "Durian ORM - WhatsApp Campaign")
    monkeypatch.setattr(main.config, "ZOHO_CRM_INTEGRATION_SOURCE_FIELD", "Integration_Source")
    assert main._campaign_integration_source(_AD_CONV) == "Durian ORM - WhatsApp Campaign"
    assert main._campaign_integration_source(_REPLY_CONV, "digital") == "Durian ORM - WhatsApp Campaign"
    # a confirmed studio visit keeps the Offline Home Studio Blueprint (field empty)
    assert main._campaign_integration_source(_AD_CONV, "offline") == ""
    assert main._campaign_integration_source(_PLAIN_CONV) == ""
    monkeypatch.setattr(main.config, "ZOHO_CRM_INTEGRATION_SOURCE_FIELD", "")
    assert main._campaign_integration_source(_AD_CONV) == ""


def test_campaign_deal_fields_follow_client_convention(monkeypatch):
    import main
    for k, v in {"ZOHO_CRM_CAMPAIGN_SOURCE_FIELD": "Campaign_Source1",
                 "ZOHO_CRM_CAMPAIGN_MEDIUM_FIELD": "Campaign_Medium",
                 "ZOHO_CRM_CAMPAIGN_NAME_FIELD": "Campaign_Name",
                 "ZOHO_CRM_CAMPAIGN_CONTENT_FIELD": "Campaign_Content"}.items():
        monkeypatch.setattr(main.config, k, v)
    f = main._campaign_deal_fields(main._whatsapp_campaign(_AD_CONV))
    assert f == {"Campaign_Source1": "whatsapp", "Campaign_Medium": "click-to-whatsapp ad",
                 "Campaign_Name": "Trusted By 3.2 Million Customers.",
                 "Campaign_Content": "Customised for your home. Delivered by Durian."}
    # empty values and switched-off fields are left out; long values capped
    f2 = main._campaign_deal_fields({"medium": "whatsapp campaign", "name": "x" * 400, "content": ""})
    assert len(f2["Campaign_Name"]) == 255 and "Campaign_Content" not in f2
    monkeypatch.setattr(main.config, "ZOHO_CRM_CAMPAIGN_NAME_FIELD", "")
    assert "Campaign_Name" not in main._campaign_deal_fields({"medium": "m", "name": "n"})
    assert main._campaign_deal_fields({}) == {}


def test_campaign_marker_never_costs_the_deal(monkeypatch):
    import asyncio
    import main
    monkeypatch.setattr(main.config, "ZOHO_CRM_INTEGRATION_SOURCE_FIELD", "Integration_Source")
    calls = []

    async def create(fields):
        calls.append(dict(fields))
        if "Integration_Source" in fields:
            raise RuntimeError('INVALID_DATA {"api_name":"Integration_Source"}')
        return {"id": "D9"}
    fields = {"Business_Type": "Retail", "Integration_Source": "Durian ORM - WhatsApp Campaign"}
    deal = asyncio.run(main._create_deal_keeping_campaign_optional(create, fields, True, 7))
    assert deal == {"id": "D9"} and len(calls) == 2
    assert "Integration_Source" not in calls[1] and calls[1]["Business_Type"] == "Retail"

    async def broken(fields):
        raise RuntimeError("MANDATORY_NOT_FOUND Owner")
    try:   # any other failure is still a real failure
        asyncio.run(main._create_deal_keeping_campaign_optional(broken, {}, True, 7))
        raise AssertionError("expected HTTPException")
    except main.HTTPException as e:
        assert "MANDATORY_NOT_FOUND" in str(e.detail)


def test_backfill_marks_campaign_deals_only(monkeypatch):
    import asyncio
    import backfill_campaign_deals as bf
    import main
    monkeypatch.setattr(main.config, "ZOHO_CRM_SOURCE_FIELD", "Lead_Source")
    monkeypatch.setattr(main.config, "ZOHO_CRM_INTEGRATION_SOURCE_FIELD", "Integration_Source")
    monkeypatch.setattr(main.config, "ZOHO_CRM_CAMPAIGN_INTEGRATION_SOURCE",
                        "Durian ORM - WhatsApp Campaign")
    convs = [dict(_AD_CONV, id=1, custom_attributes=dict(_AD_CONV["custom_attributes"], crm_deal_id="D1")),
             dict(_REPLY_CONV, id=2, custom_attributes={"crm_deal_id": "D2"}),
             dict(_PLAIN_CONV, id=3, custom_attributes={"crm_deal_id": "D3"}),
             dict(_AD_CONV, id=4, custom_attributes=dict(_AD_CONV["custom_attributes"], crm_deal_id="D4"))]

    async def list_deals(max_age_days=90):
        return convs
    monkeypatch.setattr(bf.chatwoot, "list_deal_conversations", list_deals)
    existing = {"D1": None, "D2": "#MV_Some_Other_Form",      # D2: another integration's value
                "D4": "DurianORM"}                            # D4: our own FHC digital value
    monkeypatch.setattr(main.config, "ZOHO_CRM_BLUEPRINT_DIGITAL_VALUE", "DurianORM")

    async def crm(method, path, params=None, json_body=None):
        return {"data": [{"Integration_Source": existing[path.rsplit("/", 1)[-1]]}]}
    monkeypatch.setattr(bf.zoho_crm, "_crm_request", crm)
    writes = {}

    async def update(deal_id, fields):
        writes.setdefault(deal_id, {}).update(fields)
        return True
    monkeypatch.setattr(bf.zoho_crm, "update_deal", update)
    asyncio.run(bf.run(apply=False))
    assert writes == {}                                     # list-only by default
    asyncio.run(bf.run(apply=True))
    assert set(writes) == {"D1", "D2", "D4"}                # the plain deal is untouched
    assert writes["D1"]["Integration_Source"] == "Durian ORM - WhatsApp Campaign"
    assert writes["D1"]["Lead_Source"] == "Whats App Or SMS"
    assert writes["D1"]["Campaign_Source1"] == "whatsapp"
    assert "Integration_Source" not in writes["D2"]         # never overwrites another value
    assert writes["D2"]["Campaign_Name"] == "Grand Festive Offer"
    # our own "DurianORM" is replaced, as a new campaign deal would get
    assert writes["D4"]["Integration_Source"] == "Durian ORM - WhatsApp Campaign"


def test_campaign_fields_are_single_line(monkeypatch):
    import main
    monkeypatch.setattr(main.config, "ZOHO_CRM_CAMPAIGN_CONTENT_FIELD", "Campaign_Content")
    f = main._campaign_deal_fields({"medium": "whatsapp campaign",
                                    "content": "*FLAT 50%* OFF\n\nGive Your Home  A New Look 🪔"})
    assert f["Campaign_Content"] == "*FLAT 50%* OFF Give Your Home A New Look 🪔"


# --- CRM stage → WhatsApp template automation (crm_automation.py) -------------

_WS_TPL = {"name": "durian_ws_qualified", "language": "en_US", "status": "APPROVED",
           "category": "UTILITY", "components": [{"type": "BODY", "text":
           "Hello {{1}}, our workspace expert, {{2}}, will reach out to you shortly."}]}
_WS_FU1 = {"name": "durian_ws_followup_1", "language": "en_US", "status": "APPROVED",
           "category": "MARKETING", "components": [{"type": "BODY", "text":
           "Hello {{1}}, your expert {{2}} is on {{3}}."}]}


def _ws_deal(stage="Qualified Deal (Please Select This )", **extra):
    d = {"id": "555", "Stage": stage, "Deal_Name": "Ravi — DurianORM · WhatsApp",
         "Contact_Name": {"name": "Ravi Kumar", "id": "9"}, "Mobile": "98765 43210",
         "Owner": {"name": "Neha Singh", "email": "neha@durian.in"}, "Deal_No": "D-1",
         "Integration_Source": "Workspaces Office Form"}
    d.update(extra)
    return d


class _AutoCW:
    def __init__(self, templates=None, consent=""):
        self.templates = templates if templates is not None else [_WS_TPL, _WS_FU1]
        self.consent, self.sent, self.notes = consent, [], []

    async def list_whatsapp_templates(self, inbox_id):
        return self.templates

    async def ensure_whatsapp_conversation(self, inbox_id, phone, name=""):
        return 70, 700

    async def latest_whatsapp_consent(self, contact_id, inbox_id):
        return self.consent

    async def send_template_message(self, conv_id, content, template_params):
        self.sent.append((conv_id, content, template_params))
        return {"id": 1}

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)


def _auto_setup(monkeypatch, rules, deal=None, cw=None, enabled=True, team=None):
    import os
    import tempfile
    import config_store
    import crm_automation as ca
    d = tempfile.mkdtemp()
    monkeypatch.setattr(config_store, "_DB_PATH", os.path.join(d, "cs.db"))
    monkeypatch.setattr(ca, "_DB_PATH", os.path.join(d, "auto.db"))
    config_store.init()
    ca.init()
    config_store.publish({"enabled": enabled, "inbox_id": 25, "team": team or [], "rules": rules},
                         domain=config_store.CRM_AUTOMATION)
    cw = cw or _AutoCW()
    monkeypatch.setattr(ca, "chatwoot", cw)
    state = {"deal": deal or _ws_deal()}

    async def fetch(deal_id):
        return state["deal"]
    monkeypatch.setattr(ca, "_fetch_deal", fetch)
    return ca, cw, state


def _rule(rid="ws_qualified", stage="Qualified Deal (Please Select This )",
          template="durian_ws_qualified", delay=0, variables=None, **extra):
    r = {"id": rid, "name": rid, "enabled": True, "stage": stage,
         "match": [{"field": "Integration_Source", "op": "contains", "value": "workspaces"}],
         "template": template, "language": "", "delay_hours": delay,
         "only_if_still_in_stage": True, "header_media_url": "",
         "variables": variables if variables is not None else
         [{"source": "contact_name", "fallback": "there"},
          {"source": "owner_name", "fallback": "our workspace expert"}]}
    r.update(extra)
    return r


def test_auto_matching_and_variables():
    import crm_automation as ca
    deal = _ws_deal()
    assert ca.stage_matches({"stage": "qualified deal  (please select THIS )"}, deal)
    assert not ca.stage_matches({"stage": "Follow Up"}, deal)
    assert ca.filters_match({"match": [{"field": "Integration_Source", "op": "contains",
                                        "value": "WORKSPACES"}]}, deal)[0]
    ok, why = ca.filters_match({"match": [{"field": "Owner", "op": "equals", "value": "Someone"}]}, deal)
    assert not ok and "Neha Singh" in why                       # object fields compare by name
    assert ca.filters_match({"match": [{"field": "", "op": "equals", "value": ""}]}, deal)[0]
    assert ca.filters_match({"match": [{"field": "Mobile", "op": "not_empty"}]}, deal)[0]
    team = [{"owner": "neha singh", "phone": "+91 99999 11111"}]
    rv = lambda src, fb="": ca.resolve_variable({"source": src, "fallback": fb}, deal, team)
    assert rv("contact_name") == "Ravi Kumar" and rv("first_name") == "Ravi"
    assert rv("owner_name") == "Neha Singh" and rv("owner_phone") == "+91 99999 11111"
    assert rv("field:Integration_Source") == "Workspaces Office Form" and rv("text:hi") == "hi"
    assert ca.resolve_variable({"source": "owner_phone", "fallback": "this number"}, deal, []) \
        == "this number"
    for raw, want in [("98765 43210", "919876543210"), ("+91-98765-43210", "919876543210"),
                      ("098765 43210", "919876543210"), ("12345", "")]:
        assert ca.customer_phone({"Mobile": raw}) == want, raw


def test_auto_sends_once_into_the_conversation(monkeypatch):
    import asyncio
    ca, cw, _ = _auto_setup(monkeypatch, [_rule()])
    out = asyncio.run(ca.handle_deal("555"))
    assert out[0]["status"] == "sent" and len(cw.sent) == 1
    conv, content, params = cw.sent[0]
    assert conv == 700 and params["name"] == "durian_ws_qualified"
    assert params["processed_params"] == {"body": {"1": "Ravi Kumar", "2": "Neha Singh"}}
    assert content.startswith("Hello Ravi Kumar, our workspace expert, Neha Singh")
    assert "CRM automation" in cw.notes[0]
    # Zoho retries / the stage is saved again → never a second message
    assert asyncio.run(ca.handle_deal("555"))[0]["status"] == "already handled"
    assert len(cw.sent) == 1


def test_auto_respects_switch_filter_and_stop(monkeypatch):
    import asyncio
    ca, cw, state = _auto_setup(monkeypatch, [_rule()], enabled=False)
    assert asyncio.run(ca.handle_deal("555"))[0]["detail"] == "automation is switched off"
    ca, cw, state = _auto_setup(monkeypatch, [_rule()], deal=_ws_deal(Integration_Source="FB form"))
    assert asyncio.run(ca.handle_deal("555")) == [] and cw.sent == []   # not a Workspaces deal
    ca, cw, state = _auto_setup(monkeypatch, [_rule()], cw=_AutoCW(consent="OPTED_OUT"))
    res = asyncio.run(ca.handle_deal("555"))[0]
    assert res["status"] == "skipped" and "STOP" in res["detail"] and cw.sent == []


def test_auto_followups_scheduled_and_cancelled_when_deal_moves(monkeypatch):
    import asyncio
    phone_vars = [{"source": "contact_name", "fallback": "there"},
                  {"source": "owner_name", "fallback": "our expert"},
                  {"source": "owner_phone", "fallback": "this number"}]
    rules = [_rule("fu1", "Follow Up", "durian_ws_followup_1", 0, phone_vars),
             _rule("fu2", "Follow Up", "durian_ws_followup_1", 48, phone_vars)]
    ca, cw, state = _auto_setup(monkeypatch, rules, deal=_ws_deal("Follow Up"),
                                team=[{"owner": "Neha Singh", "phone": "+91 99999 11111"}])
    out = {r["rule"]: r["status"] for r in asyncio.run(ca.handle_deal("555"))}
    assert out == {"fu1": "sent", "fu2": "scheduled"} and len(cw.sent) == 1
    assert cw.sent[0][2]["processed_params"]["body"]["3"] == "+91 99999 11111"
    assert asyncio.run(ca.run_due()) == 0                      # not due yet
    with ca._conn() as c:                                      # fast-forward the clock
        c.execute("UPDATE runs SET due_at = '2000-01-01T00:00:00+00:00' WHERE rule_id = 'fu2'")
    state["deal"] = _ws_deal("Interested")                     # lead moved on
    assert asyncio.run(ca.run_due()) == 1 and len(cw.sent) == 1
    fu2 = next(r for r in ca.recent_runs() if r["rule_id"] == "fu2")
    assert fu2["status"] == "cancelled" and "Interested" in fu2["detail"]


def test_auto_due_followup_sends_when_still_in_stage(monkeypatch):
    import asyncio
    ca, cw, state = _auto_setup(monkeypatch, [_rule("fu2", "Follow Up", delay=48)],
                                deal=_ws_deal("Follow Up"))
    asyncio.run(ca.handle_deal("555"))
    with ca._conn() as c:
        c.execute("UPDATE runs SET due_at = '2000-01-01T00:00:00+00:00'")
    asyncio.run(ca.run_due())
    assert len(cw.sent) == 1 and ca.recent_runs()[0]["status"] == "sent"


def test_auto_refuses_mismatched_or_unready_templates(monkeypatch):
    import asyncio
    ca, cw, _ = _auto_setup(monkeypatch, [_rule(variables=[{"source": "contact_name"}])])
    assert asyncio.run(ca.handle_deal("555"))[0]["status"] == "failed"         # 1 var vs 2
    img = dict(_WS_TPL, components=[{"type": "HEADER", "format": "IMAGE"}] + _WS_TPL["components"])
    ca, cw, _ = _auto_setup(monkeypatch, [_rule()], cw=_AutoCW(templates=[img]))
    res = asyncio.run(ca.handle_deal("555"))[0]
    assert res["status"] == "skipped" and "media URL" in res["detail"]
    ca, cw, _ = _auto_setup(monkeypatch, [_rule()], cw=_AutoCW(templates=[]))
    assert "not approved" in asyncio.run(ca.handle_deal("555"))[0]["detail"]
    ca, cw, _ = _auto_setup(monkeypatch, [_rule()], deal=_ws_deal(Mobile=""))
    assert "no valid mobile" in asyncio.run(ca.handle_deal("555"))[0]["detail"]


def test_auto_validate_and_defaults():
    import crm_automation as ca
    v = ca.validate(ca.DEFAULT_DOC)
    assert v["ok"] and len(ca.DEFAULT_DOC["rules"]) == 7
    assert not any(r["enabled"] for r in ca.DEFAULT_DOC["rules"])   # off until filters are set
    bad = {"inbox_id": 25, "rules": [_rule(stage=""), _rule(delay=9999)],
           "team": [{"owner": "X", "phone": "12"}]}
    errs = " ".join(ca.validate(bad)["errors"])
    assert "unique id" in errs and "stage" in errs and "0–720" in errs and "invalid phone" in errs
    nofilter = {"inbox_id": 25, "rules": [_rule(match=[])]}
    assert "EVERY deal" in ca.validate(nofilter)["warnings"][0]


def test_auto_webhook_security_and_dispatch(monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    monkeypatch.setattr(main.config, "CRM_AUTOMATION_WEBHOOK_SECRET", "")
    assert client.post("/crm/stage-changed?key=x", json={"deal_id": "1"}).status_code == 503
    monkeypatch.setattr(main.config, "CRM_AUTOMATION_WEBHOOK_SECRET", "s3")
    assert client.post("/crm/stage-changed?key=no", json={"deal_id": "1"}).status_code == 401
    assert client.post("/crm/stage-changed?key=s3", json={}).status_code == 422
    seen = []

    async def handle(deal_id):
        seen.append(deal_id)
        return []
    monkeypatch.setattr(main.crm_automation, "handle_deal", handle)
    r = client.post("/crm/stage-changed?key=s3", data={"id": "3608871000639735034"})  # Zoho form post
    assert r.status_code == 200 and r.json()["deal_id"] == "3608871000639735034"
    assert seen == ["3608871000639735034"]
    # the other ways a Zoho webhook can carry the deal id
    client.post("/crm/stage-changed?key=s3", json={"deal_id": "111"})
    client.post("/crm/stage-changed?key=s3&deal_id=222")
    client.post("/crm/stage-changed?key=s3", files={"deal_id": (None, "333")})   # form-data
    assert seen[1:] == ["111", "222", "333"]
def test_price_shorthand_comment_gets_dm_invite():
    # convs 9477 / 10284: "Pp" got "Welcome to Durian Furniture. How may we assist you today?"
    for pp in ("Pp", "pp", "PP?", "P.P", "p p", "Pp pls", "price plz", "Price?", "rate?",
               "Pp 🙏", "pp please"):
        assert sa.is_price_shorthand(pp), pp
    for other in ("Hi", "pretty", "Happy", "price of this sofa in Delhi?", "Pappu", "app",
                  "nice price", ""):
        assert not sa.is_price_shorthand(other), other
    greet = "Hello! Welcome to Durian Furniture. How may we assist you today? – Team Durian"
    assert sa.price_comment_line("Pp", greet) == sa._COMMENT_PRICE_LINE
    assert sa.comment_violation(sa._COMMENT_PRICE_LINE) == ""
    # a reply that already invites a DM, or a comment that isn't shorthand, is kept
    assert sa.price_comment_line("Pp", "Thank you! Please DM us for the Quinn recliner's price.") == ""
    assert sa.price_comment_line("Love this!", greet) == ""
    assert sa.is_low_value_comment("Pp") is False


def test_off_scope_advice_is_replaced():
    # FHC WhatsApp, 24 Oct: "what to do if I am feeling tired"
    tired = ("Please take a break, drink some water, and rest in a comfortable place. "
             "If the tiredness comes with chest pain, please seek medical help promptly.")
    assert sa.off_scope_line(tired) == sa._OFF_SCOPE_LINE
    assert sa.off_scope_line("You should consult a lawyer for that.") == sa._OFF_SCOPE_LINE
    # in-scope replies, small talk and kitchen fittings pass untouched
    for ok in ("Sorry to hear that, Vaibhav. Please take some rest — we can continue "
               "with your kitchen redesign whenever you feel ready.",
               "We can add a tall unit with a medicine drawer next to the fridge.",
               "Our Kirti Nagar showroom is open 11 AM to 9 PM, all days.",
               "EMI options available"):
        assert sa.off_scope_line(ok) == ""
    # the fixed lines themselves pass the other guardrails
    line = sa._OFF_SCOPE_LINE
    assert not sa.comment_violation(line) and not sa.link_violation(line)
    assert sa.off_scope_line(line) == ""


def test_home_studio_deals_are_created_digital(monkeypatch):
    # Agent / Create-Deal FHC deals carried no fhc_blueprint, so Integration
    # Source stayed empty and Zoho put them in the Offline Blueprint.
    import main
    monkeypatch.setattr(main.config, "ZOHO_CRM_HOME_STUDIO_LAYOUT", "Home Studio")
    assert main._blueprint_mode({}, "Home Studio") == "digital"
    assert main._blueprint_mode({"fhc_blueprint": "Digital"}, "Home Studio") == "digital"
    assert main._blueprint_mode({"fhc_blueprint": "offline"}, "Home Studio") == "offline"  # studio visit
    assert main._blueprint_mode({}, "Standard") == ""                                       # not FHC
    monkeypatch.setattr(main.config, "ZOHO_CRM_HOME_STUDIO_LAYOUT", "")
    assert main._blueprint_mode({}, "Standard") == ""


def test_furniture_account_defaults_to_furniture():
    now = sa.datetime(2026, 10, 26, 12, 0, tzinfo=sa.timezone.utc)
    furn = sa._system_prompt("dm", "Whatsapp Furniture", "furniture", now, "", "", 1,
                             channel="whatsapp")
    assert "THIS IS THE FURNITURE ACCOUNT" in furn and "NEVER offer doors" in furn
    fhc = sa._system_prompt("dm", "Whatsapp FHC", "fhc", now, "", "", 1, channel="whatsapp")
    assert "THIS IS THE FURNITURE ACCOUNT" not in fhc
    assert "FULL HOME CUSTOMISATION ACCOUNT" in fhc


def test_owner_sync_swaps_dead_owners_and_audits(monkeypatch):
    """A deactivated/deleted Zoho owner never fails a deal (conv 11191): a
    same-named successor, else the reporting manager, else the fallback."""
    import owner_sync
    monkeypatch.setattr(owner_sync, "_state", {
        "at": "2026-10-28T08:45:00+05:30",
        "active": {"602": {"id": "602", "name": "Delhi Private Project", "email": "bhanu@x.in", "reports_to": ""},
                   "900": {"id": "900", "name": "Franchise Team", "email": "fr@x.in", "reports_to": ""},
                   "1":   {"id": "1", "name": "Saharsh", "email": "s@x.in", "reports_to": ""}},
        "inactive": {"333": {"id": "333", "name": "Darbhanga Dealer", "email": "d@x.in", "reports_to": "900"}},
        "names": {"355": "Delhi Private Project_", "278": "RAJ KUMAR SINGH"}})
    fallback = {"owner_id": "1", "owner_email": "s@x.in"}
    assert owner_sync.status("602") == "active" and owner_sync.status("333") == "deactivated"
    assert owner_sync.status("355") == "deleted"
    # live owner kept untouched
    keep = {"owner_id": "602", "owner_email": "bhanu@x.in", "location": "private:Delhi"}
    assert owner_sync.live_owner(keep, fallback) == (keep, "")
    # deleted, renamed role → same-name successor (keeps location/vertical)
    new, note = owner_sync.live_owner({"owner_id": "355", "location": "private:Delhi"}, fallback)
    assert new["owner_id"] == "602" and new["location"] == "private:Delhi"
    assert "same Zoho user name" in note
    # deactivated → reporting manager
    assert owner_sync.live_owner({"owner_id": "333"}, fallback)[0]["owner_id"] == "900"
    # deleted, no successor → fallback
    new, note = owner_sync.live_owner({"owner_id": "278"}, fallback)
    assert new == fallback and "fallback" in note
    # no snapshot yet → never swap
    monkeypatch.setattr(owner_sync, "_state", {"at": None, "active": {}})
    assert owner_sync.status("355") == "unknown"
    assert owner_sync.live_owner({"owner_id": "355"}, fallback)[0]["owner_id"] == "355"


def test_owner_sync_audit_lists_routing_and_store_slots(monkeypatch):
    import owner_sync
    monkeypatch.setattr(owner_sync, "_state", {
        "at": "x", "active": {"602": {"id": "602", "name": "Delhi Private Project", "email": "b@x.in", "reports_to": ""}},
        "inactive": {}, "names": {"355": "Delhi Private Project_"}})
    monkeypatch.setattr(owner_sync.classifier, "get_routing_rules", lambda *a, **k: {
        "crm_owner_routing_private": {"Delhi": {"owner_email": "a@x.in", "owner_id": "355"}},
        "crm_owner_routing_govt_bulk": {"Kolkata": [{"owner_email": "b@x.in", "owner_id": "602"},
                                                    {"owner_email": "v@x.in", "owner_id": "777"}]},
        "crm_owner_routing": {"Parked": {"owner_email": "p@x.in", "owner_id": "888"}}})
    monkeypatch.setattr(owner_sync.store_locator, "_stores", lambda: [
        {"id": "s1", "name": "Ludhiana Store", "crm_owner_id": "246"}, {"id": "s2", "name": "No owner"}])
    rep = owner_sync.audit()
    rows = {(p["kind"], p["label"]): p for p in rep["problems"]}
    assert set(rows) == {("routing", "Delhi"), ("routing", "Kolkata · #2"), ("store", "Ludhiana Store")}
    assert rows[("routing", "Delhi")]["successor"]["id"] == "602"
    assert rows[("store", "Ludhiana Store")]["status"] == "deleted"
    assert "888" not in {p["owner_id"] for p in rep["problems"]}   # parked retail matrix ignored


def test_dead_bulk_owner_falls_back_to_saharsh(monkeypatch):
    import main
    rules = {"crm_owner_routing_govt_bulk": {
        "Uttrakhand and Punjab": [{"owner_email": "raj@x.in", "owner_id": "278"}],
        main.SAHARSH_TERRITORY_KEY: [{"owner_email": "saharsh@x.in", "owner_id": "1"}]},
        "crm_owner_routing_fallback": {"owner_email": "hello@x.in", "owner_id": "9"}}
    monkeypatch.setattr(main.classifier, "_ROUTING_RULES", rules)
    assert main._dead_owner_fallback({"owner_id": "278"})["owner_id"] == "1"
    assert main._dead_owner_fallback({"owner_id": "555"})["owner_id"] == "9"


def test_store_map_link_is_the_stores_own_google_listing(monkeypatch):
    """FHC Kirti Nagar's card opened the Durian Furniture store in the same
    building — each store now gets its own GBP listing's link (same vertical)."""
    import store_maps
    import store_locator
    L = lambda title, pin, link, locality="": store_maps._listing({    # noqa: E731
        "title": title, "metadata": {"mapsUri": link},
        "storefrontAddress": {"postalCode": pin, "locality": locality}})
    listings = [L("Durian Furniture - Delhi - Kirti Nagar", "110015", "cid-furn-kn"),
                L("Durian Full Home Customisation", "110015", "cid-fhc-kn", "Delhi"),
                L("Durian Experience Centre for Doors - Kirti Nagar", "110015", "cid-doors-kn"),
                L("Durian Furniture - Noida - Sector 10", "201301", "cid-furn-n10"),
                L("Durian Furniture - Noida - Sector 49", "201303", "cid-furn-n49"),
                L("Durian Project Office- Bhopal", "462001", "cid-office")]
    assert listings[-1] is None                       # project offices aren't stores
    monkeypatch.setattr(store_maps, "_listings", [x for x in listings if x])
    monkeypatch.setattr(store_maps.config, "STORE_MAPS_FROM_GBP", True)
    fhc = {"id": "f", "primary_vertical": "fhc", "name": "Durian Full Home Customisation - Delhi - Kirti Nagar",
           "pincode": "110015", "map_url": "short-link-of-the-furniture-store"}
    assert store_maps.link_for(fhc) == "cid-fhc-kn"
    assert store_maps.link_for({**fhc, "primary_vertical": "doors", "name": "Durian Doors - Kirti Nagar"}) == "cid-doors-kn"
    # a wrong pincode in the sheet never pulls another place's listing
    noida10 = {"primary_vertical": "furniture", "name": "Durian Furniture - Noida - Sector 10", "pincode": "110015"}
    assert store_maps.link_for(noida10) == "cid-furn-n10"
    assert store_maps.link_for({"primary_vertical": "furniture", "name": "Durian Furniture - Gurgaon JMD",
                                "pincode": "110015"}) == ""
    # the card uses it; a map_url set in Settings still wins; kill switch
    monkeypatch.setattr(store_locator, "_floor", lambda: [fhc])
    monkeypatch.setattr(store_locator, "_override_doc", lambda: {})
    assert store_locator._stores()[0]["map_url"] == "cid-fhc-kn"
    monkeypatch.setattr(store_locator, "_override_doc", lambda: {"stores": {"f": {"map_url": "set-in-settings"}}})
    assert store_locator._stores()[0]["map_url"] == "set-in-settings"
    monkeypatch.setattr(store_maps.config, "STORE_MAPS_FROM_GBP", False)
    assert store_maps.link_for(fhc) == ""


def test_idle_followup_then_resolve_timeline(monkeypatch):
    """WhatsApp chats the AI owns: one follow-up ~20h after the customer's last
    message (inside the 24h window, never at night), resolved 24h after it."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import idle_followup as idle
    monkeypatch.setattr(idle.config, "IDLE_FOLLOWUP_AFTER_HOURS", 20.0)
    monkeypatch.setattr(idle.config, "IDLE_RESOLVE_AFTER_HOURS", 24.0)
    monkeypatch.setattr(idle.config, "IDLE_SWEEP_MINUTES", 15)
    ist = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()  # noqa: E731
    H = 3600
    t_in = ist(10, 14)                                   # customer wrote 2 PM
    msgs = [{"id": 1, "message_type": 0, "created_at": t_in, "content": "kitchen price?"},
            {"id": 2, "message_type": 1, "created_at": t_in + 60, "content": "Sure — your city?"},
            {"id": 3, "message_type": 2, "private": True, "created_at": t_in + 70, "content": "note"}]
    conv = {"custom_attributes": {}, "meta": {}, "labels": []}
    assert idle.decide(conv, msgs, t_in + 10 * H) == ""              # too early
    assert idle.decide(conv, msgs, t_in + 20 * H) == "follow_up"    # 10 AM next day
    # followed up → resolve only 24h after the follow-up
    conv["custom_attributes"] = {"idle_followup_for": "1", "idle_followup_at": t_in + 20 * H}
    assert idle.decide(conv, msgs, t_in + 30 * H) == ""
    assert idle.decide(conv, msgs, t_in + 44 * H) == "resolve"
    # the customer replied → waiting on us, never touched
    assert idle.decide(conv, msgs + [{"id": 4, "message_type": 0, "created_at": t_in + 21 * H}],
                       t_in + 50 * H) == ""
    # backlog: window long closed, no follow-up possible → resolve
    assert idle.decide({"custom_attributes": {}, "meta": {}}, msgs, t_in + 5 * 24 * H) == "resolve"
    # never a human's chat, a chat waiting on a team, or a stood-down chat
    human = {"custom_attributes": {}, "meta": {"assignee": {"name": "Neha Singh"}}}
    assert idle.decide(human, msgs, t_in + 20 * H) == ""
    assert idle.decide({"custom_attributes": {}, "meta": {}, "labels": ["agent-needed"]},
                       msgs, t_in + 5 * 24 * H) == ""
    assert idle.decide({"custom_attributes": {"agent_mode_standdown": True}, "meta": {}},
                       msgs, t_in + 20 * H) == ""
    # outbound-only thread (campaign the customer never answered) → left alone
    assert idle.decide({"custom_attributes": {}, "meta": {}}, msgs[1:], t_in + 5 * 24 * H) == ""
    # night: customer wrote 1 AM → 20h lands at 9 PM (quiet) and the window closes
    # 12:30 AM → follow up at the last daytime sweep before 9 PM instead
    t_night = ist(11, 1)
    assert not idle.follow_up_due(ist(11, 20, 30), t_night)          # 19.5h, still daytime ahead
    assert idle.follow_up_due(ist(11, 20, 50), t_night)              # last sweep before 9 PM
    assert not idle.follow_up_due(ist(11, 22), t_night)              # never at night


def test_idle_followup_draft_falls_back_on_unsafe_text(monkeypatch):
    import asyncio
    import idle_followup as idle

    class _R:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    def client(text):
        async def create(**_):
            return _R(text)
        return type("X", (), {"chat": type("Y", (), {"completions": type("Z", (), {"create": staticmethod(create)})()})()})()
    conv = {"id": 9, "meta": {"sender": {"name": "Disha Shah"}}}
    msgs = [{"message_type": 0, "content": "modular kitchen"}, {"message_type": 1, "content": "Your city?"}]
    monkeypatch.setattr(idle.sa, "_client", client("Hi Disha, shall we share our Kirti Nagar studio details?"))
    assert asyncio.run(idle._draft(conv, msgs, "fhc")).startswith("Hi Disha, shall we")
    monkeypatch.setattr(idle.sa, "_client", client("Hi Disha, our kitchens start at ₹1,20,000!"))
    assert asyncio.run(idle._draft(conv, msgs, "fhc")) == idle._FALLBACK["fhc"].format(name=" Disha")
