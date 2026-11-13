# info@durian.in routing — its own categories + owners, separate from hello@.
# No network: the AI pick is stubbed and Chatwoot is faked.
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import info_routing as ir  # noqa: E402
import main  # noqa: E402

RULES = ir.load_rules(force=True)
INFO_INBOX = {"id": 31, "name": "Durian Info"}
HELLO_INBOX = {"id": 9, "name": "Hello Durian"}
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
FINANCE = ["jitesh@durian.in", "vikaashtulsian@durian.in", "dipesh.patel@durian.in"]
LOGISTICS = ["ganguli@durian.in", "rakesh@durian.in", "jayashree@durian.in"]


# ── The rules file ──────────────────────────────────────────────────────────

def test_rules_file_is_valid():
    assert ir.validate(RULES) == []


def test_owners_match_durians_notes():
    """Owners exactly as Durian wrote them on the review (3 Nov 2026)."""
    cats = RULES["categories"]
    owners = {k: ir.recipients(v.get("forward_to")) for k, v in cats.items()
              if v.get("action") == "forward"}
    assert owners == {
        "govt_psu_business": ["marketing@durian.in"],
        "govt_psu_notice": ["marketing@durian.in", "saharsh@durian.in"],
        "finance_bank_payments": FINANCE,
        "finance_tax_regulatory": FINANCE,
        "own_shipments": LOGISTICS,
        "pitch_raw_material": ["shivshankar.kushwah@durian.in"],
        "pitch_services": ["itsupport@durian.in"],
        "pitch_freight": LOGISTICS,
        "pitch_listings_pr": ["shilpa@durian.in"],
    }


def test_categories_without_an_owner_are_held():
    held = {k for k, v in RULES["categories"].items() if v.get("action") == "hold"}
    assert held == {"legal_ip_notice", "customer_complaint", "business_enquiry", "oem_supply",
                    "job_application", "erp_po_copy", "internal_notice", "pitch_other",
                    "industry_events", "newsletters", "promotions_other", "group_company"}
    for k in held:
        assert not RULES["categories"][k].get("forward_to"), k


def test_validate_catches_broken_rules():
    bad = {"categories": {"x": {"action": "forward"},
                          "y": {"action": "forward", "forward_to": "not-an-email"},
                          "z": {"action": "send"}},
           "sender_rules": [{"sender": "a@b.com", "category": "nope"}]}
    problems = " ".join(ir.validate(bad))
    assert "x: forward category without forward_to" in problems
    assert "not an email address" in problems and "z: action must be" in problems
    assert "unknown category" in problems


def test_recipients_parsing():
    assert ir.recipients("a@x.in, b@x.in,, A@x.in") == ["a@x.in", "b@x.in"]
    assert ir.recipients(["a@x.in", " b@x.in "]) == ["a@x.in", "b@x.in"]
    assert ir.recipients(None) == [] and ir.recipients("") == []


# ── Sender rules (no AI) ────────────────────────────────────────────────────

@pytest.mark.parametrize("sender,expected", [
    ("erp@durian.in", "erp_po_copy"),
    ("ERP@Durian.in", "erp_po_copy"),
    ("hr.team@durian.in", "internal_notice"),
    ("no-reply@accounts.google.com", "internal_notice"),
    ("mailer-daemon@googlemail.com", "internal_notice"),
    ("postmaster@outlook.com", "internal_notice"),
    ("tenders@bel.co.in", None),
    ("someone@durianfurniture.com", None),     # look-alike domain is NOT internal
    ("", None),
    ("not-an-address", None),
])
def test_sender_rules(sender, expected):
    assert ir.sender_rule(sender, RULES) == expected


# ── Routing decision ────────────────────────────────────────────────────────

def _route(cat, conf=0.95, raw=None):
    return ir.route({"category": cat, "confidence": conf, "reason": "r",
                     "raw_category": raw or cat}, RULES, auto_confidence=0.85)


def test_confident_owned_category_forwards():
    d = _route("govt_psu_notice")
    assert d["route"] == "forward"
    assert d["forward_to"] == ["marketing@durian.in", "saharsh@durian.in"] and d["cc"] == []
    assert d["group"] == "Needs a business owner"


def test_category_without_owner_is_held():
    d = _route("customer_complaint")
    assert d["route"] == "hold" and d["forward_to"] == []


def test_low_confidence_is_left_for_a_person():
    d = _route("finance_bank_payments", conf=0.7)
    assert d["route"] == "review" and d["guess"] == "finance_bank_payments"
    assert d["forward_to"] == []


def test_fallback_is_left_for_a_person_with_the_guess():
    d = _route(ir.FALLBACK, conf=0.4, raw="pitch_services")
    assert d["route"] == "review" and d["guess"] == "pitch_services"
    d = _route(ir.FALLBACK, conf=0.0, raw=None)
    assert d["route"] == "review" and d["guess"] is None


def test_labels():
    assert ir.label_for("govt_psu_business") == "info-govt-psu-business"
    assert ir.label_for(ir.FALLBACK) == ir.NOT_SURE_LABEL


def test_prompt_is_info_specific():
    p = ir.build_prompt(RULES)
    assert "info@durian.in" in p and "govt_psu_notice" in p and "oem_supply" in p
    assert "product_enquiry" not in p          # hello@ categories are not offered
    assert "OEMs is still a newsletter" in p   # the CII newsletter trap


def test_note_header_for_unsure_email():
    d = _route("pitch_freight", conf=0.72)
    head = ir.note_header(d, RULES)[0]
    assert "not sure" in head and "Freight and logistics companies (72%)" in head


def test_forward_body_tells_the_owner_how_to_reply():
    b = ir.forward_body("Asha", "asha@vendor.com", "Rates", "trail text")
    assert "received at info@durian.in from Asha" in b
    assert "please write to asha@vendor.com directly" in b
    assert "trail text" in b and b.endswith("Team Durian")
    assert "newest email is at the end" in ir.forward_body("A", "a@x.com", "S", "t", followup=True)


# ── Follow-up guard ─────────────────────────────────────────────────────────

FWD_STATE = {"forwarded": True, "forward_to": ["marketing@durian.in"]}


def _check(state, sender, mid=7, now=NOW):
    return ir.followup_check(state, sender, mid, now, max_per_day=3,
                             internal_domains=("durian.in",))


def test_followup_from_outside_sender_is_forwarded():
    assert _check(FWD_STATE, "tenders@npcil.co.in") == (True, "")


@pytest.mark.parametrize("state,sender,why", [
    ({"forwarded": False, "forward_to": ["m@durian.in"]}, "x@y.com", "not_forwarded"),
    ({"forwarded": True, "forward_to": []}, "x@y.com", "not_forwarded"),
    (FWD_STATE, "", "no_sender"),
    (FWD_STATE, "marketing@durian.in", "internal_sender"),     # owner replying to the forward
    (FWD_STATE, "mailer-daemon@googlemail.com", "automated_sender"),   # bounce loop guard
    (FWD_STATE, "noreply@gem.gov.in", "automated_sender"),
    ({**FWD_STATE, "followup_ids": [7]}, "x@y.com", "already_forwarded"),
])
def test_followup_blocked(state, sender, why):
    assert _check(state, sender) == (False, why)


def test_followup_daily_cap_and_recovery():
    times = [(NOW - timedelta(hours=h)).isoformat() for h in (1, 2, 3)]
    state = {**FWD_STATE, "followup_times": times}
    assert _check(state, "x@y.com") == (False, "daily_cap")
    assert _check(state, "x@y.com", now=NOW + timedelta(hours=22)) == (True, "")


def test_record_followup_is_bounded():
    s = dict(FWD_STATE)
    for i in range(30):
        s = ir.record_followup(s, i, NOW)
    assert len(s["followup_ids"]) == 20 and s["followup_ids"][-1] == 29
    assert len(s["followup_times"]) == 10 and s["followup_count"] == 30


# ── The handler (Chatwoot faked, AI stubbed) ────────────────────────────────

class FakeChatwoot:
    def __init__(self, attrs=None, sender_email="tenders@pwd.delhi.gov.in"):
        self.attrs = dict(attrs or {})
        self.sender_email = sender_email
        self.sent, self.notes, self.labels, self.status = [], [], [], "open"

    async def get_conversation(self, conv_id):
        return {"id": conv_id, "custom_attributes": dict(self.attrs),
                "additional_attributes": {"mail_subject": "Show Cause Notice"},
                "meta": {"sender": {"name": "PWD Delhi", "email": self.sender_email}}}

    async def get_conversation_messages(self, conv_id):
        return [
            {"id": 1, "message_type": 0, "content": "You failed to deliver.",
             "created_at": 1794000000, "sender": {"email": self.sender_email},
             "content_attributes": {"email": {"from": [self.sender_email]}}},
            {"id": 2, "message_type": 0, "content": "Owner's reply to the forward",
             "created_at": 1794000100, "sender": {"email": "marketing@durian.in"},
             "content_attributes": {"email": {"from": ["marketing@durian.in"]}}},
        ]

    async def send_outgoing_message(self, conv_id, content, **kw):
        self.sent.append({"content": content, **kw})
        return {"id": 99}

    async def post_private_note(self, conv_id, content):
        self.notes.append(content)

    async def ensure_label(self, title, color="#1f93ff", show_on_sidebar=True):
        return None

    async def add_label(self, conv_id, label):
        self.labels.append(label)

    async def merge_custom_attributes(self, conv_id, attrs):
        self.attrs.update(attrs)

    async def toggle_status(self, conv_id, status, **kw):
        self.status = status


def _event(sender="tenders@pwd.delhi.gov.in", mid=1, content="You failed to deliver."):
    return {"id": mid, "message_type": 0, "content": content, "inbox": INFO_INBOX,
            "conversation": {"id": 500}, "sender": {"email": sender},
            "content_attributes": {"email": {"from": [sender]}}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "INFO_ROUTING_ENABLED", True)
    monkeypatch.setattr(config, "INFO_ROUTING_INBOXES", {"durian info"})
    monkeypatch.setattr(config, "SILENT_EMAIL_INBOXES", {"durian info"})
    monkeypatch.setattr(config, "INFO_ROUTING_DRY_RUN", False)
    monkeypatch.setattr(config, "INFO_ROUTING_AUTO_CONFIDENCE", 0.85)
    monkeypatch.setattr(config, "INFO_ROUTING_FOLLOWUPS_ENABLED", True)
    monkeypatch.setattr(config, "INFO_ROUTING_FOLLOWUP_MAX_PER_DAY", 3)
    monkeypatch.setattr(config, "RESOLVE_AFTER_FORWARD", True)
    monkeypatch.setattr(main, "_LOCAL_FORWARD_OVERRIDE", "")
    main._INFO_LABELS_ENSURED.clear()
    calls = []

    def stub(category, conf=0.95):
        async def fake_classify(content, sender_email="", subject="", lf_parent=None):
            calls.append(subject)
            return {"category": category, "confidence": conf, "reason": "AI reason.",
                    "alternatives": [], "raw_category": category}
        monkeypatch.setattr(ir, "classify", fake_classify)
    return stub, calls


def _run(fake, monkeypatch, event):
    monkeypatch.setattr(main, "chatwoot", fake)
    return asyncio.run(main.handle_message_created(event))


def test_live_forward_sends_trail_tags_and_resolves(env, monkeypatch):
    stub, _ = env
    stub("govt_psu_notice")
    fake = FakeChatwoot()
    out = _run(fake, monkeypatch, _event())
    assert out["forwarded"] is True
    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent["to_emails"] == "marketing@durian.in, saharsh@durian.in"
    assert sent["cc_emails"] is None
    assert "You failed to deliver." in sent["content"]
    assert "Owner's reply to the forward" not in sent["content"]   # our people's mail left out
    assert "info-govt-psu-notice" in fake.labels and "auto-forwarded" in fake.labels
    assert fake.status == "resolved"
    st = fake.attrs["info_routing"]
    assert st["forwarded"] is True and st["forward_to"] == ["marketing@durian.in", "saharsh@durian.in"]
    assert "📨 Forwarded to marketing@durian.in, saharsh@durian.in." in fake.notes[0]


def test_trial_run_sends_nothing(env, monkeypatch):
    stub, _ = env
    stub("pitch_services")
    monkeypatch.setattr(config, "INFO_ROUTING_DRY_RUN", True)
    fake = FakeChatwoot(sender_email="sales@telco.com")
    _run(fake, monkeypatch, _event("sales@telco.com"))
    assert fake.sent == [] and fake.status == "open"
    assert "would forward to itsupport@durian.in. Nothing was sent." in fake.notes[0]
    assert fake.attrs["info_routing"]["dry_run"] is True
    assert fake.attrs["info_routing"]["forwarded"] is False
    assert "auto-forwarded" not in fake.labels


def test_category_without_owner_is_left_open(env, monkeypatch):
    stub, _ = env
    stub("customer_complaint")
    fake = FakeChatwoot(sender_email="buyer@gmail.com")
    _run(fake, monkeypatch, _event("buyer@gmail.com"))
    assert fake.sent == [] and fake.status == "open"
    assert "No owner is set for this category yet" in fake.notes[0]
    assert fake.labels == ["info-customer-complaint"]


def test_unsure_email_gets_only_the_not_sure_label(env, monkeypatch):
    stub, _ = env
    stub("finance_bank_payments", conf=0.7)
    fake = FakeChatwoot(sender_email="x@bank.com")
    _run(fake, monkeypatch, _event("x@bank.com"))
    assert fake.sent == [] and fake.status == "open"
    assert fake.labels == [ir.NOT_SURE_LABEL]
    assert "not sure" in fake.notes[0] and "Bank, payments and bills (70%)" in fake.notes[0]


def test_erp_copy_is_sorted_without_the_ai(env, monkeypatch):
    stub, calls = env
    stub("govt_psu_business")
    fake = FakeChatwoot(sender_email="erp@durian.in")
    _run(fake, monkeypatch, _event("erp@durian.in"))
    assert calls == []                                  # no AI call
    assert fake.sent == [] and fake.labels == ["info-erp-po-copy"]


def test_followup_from_sender_is_forwarded_once(env, monkeypatch):
    stub, calls = env
    stub("govt_psu_notice")
    state = {"category": "govt_psu_notice", "route": "forward", "forwarded": True,
             "forward_to": ["marketing@durian.in", "saharsh@durian.in"], "cc": []}
    fake = FakeChatwoot(attrs={"info_routing": state})
    fake.status = "open"                               # the new email reopened it
    _run(fake, monkeypatch, _event(mid=2, content="Second reminder"))
    assert calls == []                                 # not re-classified
    assert len(fake.sent) == 1 and fake.sent[0]["to_emails"] == "marketing@durian.in, saharsh@durian.in"
    assert "arrived in a thread we forwarded to you earlier" in fake.sent[0]["content"]
    assert fake.status == "resolved"
    assert fake.attrs["info_routing"]["followup_ids"] == [2]
    # the same message again (webhook retry) → not sent twice
    _run(fake, monkeypatch, _event(mid=2, content="Second reminder"))
    assert len(fake.sent) == 1


def test_owner_reply_is_not_forwarded_and_stays_resolved(env, monkeypatch):
    stub, _ = env
    stub("govt_psu_notice")
    state = {"forwarded": True, "forward_to": ["marketing@durian.in"], "cc": []}
    fake = FakeChatwoot(attrs={"info_routing": state})
    _run(fake, monkeypatch, _event("marketing@durian.in", mid=3))
    assert fake.sent == [] and fake.status == "resolved"
    assert "replied in this thread" in fake.notes[0]


def test_bounce_is_not_forwarded_and_flagged(env, monkeypatch):
    stub, _ = env
    stub("govt_psu_notice")
    state = {"forwarded": True, "forward_to": ["jitesh@durian.in"], "cc": []}
    fake = FakeChatwoot(attrs={"info_routing": state})
    _run(fake, monkeypatch, _event("mailer-daemon@googlemail.com", mid=4))
    assert fake.sent == [] and "delivery failure" in fake.notes[0]


def test_followup_in_held_thread_does_nothing(env, monkeypatch):
    stub, _ = env
    stub("customer_complaint")
    fake = FakeChatwoot(attrs={"info_routing": {"route": "hold", "forwarded": False}})
    out = _run(fake, monkeypatch, _event("buyer@gmail.com", mid=5))
    assert out["reason"] == "info_followup_not_forwarded"
    assert fake.sent == [] and fake.notes == [] and fake.labels == []


def test_switched_off_falls_back_to_observation_only(env, monkeypatch):
    stub, calls = env
    stub("govt_psu_notice")
    monkeypatch.setattr(config, "INFO_ROUTING_ENABLED", False)
    fake = FakeChatwoot()
    out = _run(fake, monkeypatch, _event())
    assert out == {"ignored": True, "reason": "silent_inbox"}
    assert calls == [] and fake.sent == [] and fake.notes == []


def test_hello_inbox_is_never_info_routed(env):
    assert main._is_info_routing_inbox(INFO_INBOX) is True
    assert main._is_info_routing_inbox({"id": 31, "name": "durian info"}) is True
    assert main._is_info_routing_inbox(HELLO_INBOX) is False


def test_local_override_redirects_the_forward(env, monkeypatch):
    stub, _ = env
    stub("pitch_raw_material")
    monkeypatch.setattr(main, "_LOCAL_FORWARD_OVERRIDE", "me@example.com")
    fake = FakeChatwoot(sender_email="sales@machines.com")
    _run(fake, monkeypatch, _event("sales@machines.com"))
    assert fake.sent[0]["to_emails"] == "me@example.com"
