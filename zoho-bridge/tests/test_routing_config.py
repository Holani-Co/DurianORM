# Routing-config editability: AI rules, subcategory fields, disable switches and
# publish validation. No real LLM calls — the OpenAI client is faked.
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import classifier  # noqa: E402
import main  # noqa: E402


def _with_rules(doc):
    """Run the classifier against YAML ⊕ `doc`, like the Preview endpoint."""
    return classifier.set_preview_rules(classifier.merge_over_base(doc))


def _cat_prompt(doc):
    token = _with_rules(doc)
    try:
        return classifier._build_category_system_prompt(classifier.get_routing_rules())
    finally:
        classifier.reset_preview_rules(token)


# ── Prompts ────────────────────────────────────────────────────────────────

def test_category_rules_come_from_config_and_format_stays_locked():
    p = _cat_prompt({"category_ai_rules": ["Treat warranty questions as complaints."]})
    rules = p.split("\nRules:\n", 1)[1]
    assert "  • Treat warranty questions as complaints." in rules
    assert "Distinguish carefully" not in rules          # client replaced the defaults
    assert "Output a confidence score" in rules          # locked output format
    assert "alternatives:" in rules


def test_disabled_category_hidden_from_ai_and_picker():
    doc = {"categories": {"digital_marketing_seo": {"disabled": True}}}
    assert "- digital_marketing_seo (" not in _cat_prompt(doc)
    token = _with_rules(doc)
    try:
        keys = [c["category"] for c in classifier.category_choices()]
        assert "digital_marketing_seo" not in keys and "complaint" in keys
        # still displays for conversations already tagged with it
        assert classifier.category_display_name("digital_marketing_seo") != ""
    finally:
        classifier.reset_preview_rules(token)


def test_examples_setting_controls_prompt():
    token = _with_rules({"ai_examples_per_category": 6})
    try:
        p = classifier._build_category_system_prompt(classifier.get_routing_rules())
    finally:
        classifier.reset_preview_rules(token)
    block = p.split("- product_enquiry (", 1)[1].split("\n- ", 1)[0]
    assert block.count('    • "') == 6            # product_enquiry has 6 examples


def test_subcategory_prompt_has_description_examples_and_skips_disabled():
    vr = {
        "retail_furniture": {"display_name": "Retail Furniture", "keywords": ["Sofa"]},
        "mattress": {"display_name": "Mattress", "description": "Mattress-only enquiries.",
                     "keywords": ["mattress"], "examples": ["Need a king size mattress"]},
        "ecom": {"display_name": "E-commerce", "disabled": True},
    }
    p = classifier._build_vertical_prompt(vr, ["Mattress beats furniture."], 4)
    assert "- mattress (Mattress)\n    Mattress-only enquiries." in p
    assert '      • "Need a king size mattress"' in p
    assert "ecom" not in p
    assert "  • Mattress beats furniture." in p and "Output a 0.0–1.0 confidence" in p


def test_bulk_sector_rules_editable_calibration_locked():
    p = classifier._build_bulk_sector_prompt({
        "ai_rules": ["Indian Railways is government."],
        "government": {"keywords": ["Ministry"]}, "private": {"keywords": ["Pvt Ltd"]}})
    assert "Indian Railways is government." in p and "Strongest signal" not in p
    assert "Government / public-sector name signals: Ministry" in p
    assert "Be conservative: use < 0.9" in p              # locked calibration


# ── classify_email_category: only ACTIVE subcategories go downstream ─────────

def _fake_client(category, vertical):
    calls = []

    async def create(**kw):
        calls.append(kw)
        if kw.get("name") == "product-vertical-classification":
            body = {"vertical": vertical, "confidence": 0.95, "reason": "r"}
        else:
            body = {"category": category, "confidence": 0.95, "reason": "r",
                    "alternatives": []}
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(body)))])
    return SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))), calls


def _classify(monkeypatch, doc, category, vertical):
    fake, calls = _fake_client(category, vertical)
    monkeypatch.setattr(classifier, "client", fake)
    token = _with_rules(doc)
    try:
        res = asyncio.run(classifier.classify_email_category("hello", "a@b.com", "subj"))
    finally:
        classifier.reset_preview_rules(token)
    return res, calls


def test_disabled_subcategory_not_offered_or_forwarded(monkeypatch):
    doc = {"categories": {"product_enquiry": {"vertical_routing": {"ecom": {"disabled": True}}}}}
    res, calls = _classify(monkeypatch, doc, "product_enquiry", "laminate")
    vcall = next(c for c in calls if c.get("name") == "product-vertical-classification")
    enum = vcall["response_format"]["json_schema"]["schema"]["properties"]["vertical"]["enum"]
    assert "ecom" not in enum and "laminate" in enum
    assert "ecom" not in res["rule"]["vertical_routing"]
    assert res["vertical"] == "laminate" and res["action"] == "forward"


def test_new_subcategory_routes(monkeypatch):
    doc = {"categories": {"product_enquiry": {"vertical_routing": {"mattress": {
        "display_name": "Mattress", "keywords": ["mattress"],
        "forward_to": "mattress.desk@example.com"}}}}}
    res, _ = _classify(monkeypatch, doc, "product_enquiry", "mattress")
    assert res["vertical"] == "mattress"
    assert res["rule"]["forward_to"] == "mattress.desk@example.com"


def test_disabled_category_not_in_schema(monkeypatch):
    doc = {"categories": {"finance_related": {"disabled": True}}}
    _, calls = _classify(monkeypatch, doc, "complaint", "retail_furniture")
    ccall = next(c for c in calls if c.get("name") == "email-12-category-classification")
    enum = ccall["response_format"]["json_schema"]["schema"]["properties"]["category"]["enum"]
    assert "finance_related" not in enum and "complaint" in enum


def test_all_subcategories_disabled_drops_subrouting(monkeypatch):
    doc = {"categories": {"product_enquiry": {"vertical_routing": {
        k: {"disabled": True} for k in ("retail_furniture", "laminate", "ecom")}}}}
    res, calls = _classify(monkeypatch, doc, "product_enquiry", "laminate")
    assert "vertical_routing" not in res["rule"]
    assert not any(c.get("name") == "product-vertical-classification" for c in calls)


# ── Publish validation ─────────────────────────────────────────────────────

def _v(doc):
    return main._validate_routing_doc(doc)


def test_core_category_cannot_be_disabled():
    r = _v({"categories": {"complaint": {"disabled": True}}})
    assert not r["ok"] and any("can't be disabled" in e for e in r["errors"])
    assert _v({"categories": {"digital_marketing_seo": {"disabled": True}}})["ok"]


def test_new_subcategory_needs_name_and_valid_key():
    bad = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "Mattress!": {"keywords": ["x"]}}}}})
    assert not bad["ok"]
    noname = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "mattress": {"keywords": ["x"]}}}}})
    assert any("needs a name" in e for e in noname["errors"])
    reserved = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "unclear": {"display_name": "U"}}}}})
    assert not reserved["ok"]
    ok = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "mattress": {"display_name": "Mattress", "keywords": ["mattress"]}}}}})
    assert ok["ok"], ok["errors"]


def test_no_subcategories_on_sector_or_location_routed_category():
    for cat in ("project_bulk_order", "doors_veneer_plywood"):
        r = _v({"categories": {cat: {"vertical_routing": {"x": {"display_name": "X"}}}}})
        assert not r["ok"], cat


def test_ai_field_types_and_limits():
    assert not _v({"category_ai_rules": "not a list"})["ok"]
    assert not _v({"ai_examples_per_category": 0})["ok"]
    assert not _v({"ai_examples_per_category": True})["ok"]
    assert _v({"ai_examples_per_category": 8, "category_ai_rules": ["a", "b"]})["ok"]
    assert not _v({"categories": {"complaint": {"vertical_rules": "x"}}})["ok"]
    assert not _v({"categories": {"complaint": {"vertical_ambiguous": "maybe"}}})["ok"]
    assert not _v({"categories": {"project_bulk_order": {"sector_routing": {"ai_rules": 5}}}})["ok"]


def test_rule_mentioning_disabled_subcategory_warns():
    r = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "ecom": {"disabled": True}},
        "vertical_rules": ["ecom = online order."]}}})
    assert r["ok"] and any("disabled subcategory ecom" in w for w in r["warnings"])


def test_partial_edit_no_false_description_warning():
    r = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "laminate": {"cc": ["a@b.com"]}}}}})
    assert r["ok"] and not any("no description" in w for w in r["warnings"])


def test_default_subcategory_cannot_forward_or_be_disabled():
    assert "retail_furniture" in main._default_subcategories()["product_enquiry"]
    d = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "retail_furniture": {"disabled": True}}}}})
    f = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "retail_furniture": {"forward_to": "x@example.com"}}}}})
    assert not d["ok"] and not f["ok"]
    kw = _v({"categories": {"product_enquiry": {"vertical_routing": {
        "retail_furniture": {"keywords": ["Sofa", "Futon"]}}}}})
    assert kw["ok"], kw["errors"]


def test_malformed_doc_is_rejected_not_crashed():
    r = _v({"categories": {"product_enquiry": {"vertical_routing": ["x"],
                                               "vertical_rules": ["a"]}}})
    assert not r["ok"] and any("must be an object" in e for e in r["errors"])
