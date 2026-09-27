# Team forwards carry the COMPLETE email trail (client issue: a manual forward
# only sent part of the conversation). No network — Chatwoot is faked.
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chatwoot  # noqa: E402
import config  # noqa: E402
import main  # noqa: E402

CUSTOMER = "Riya Sharma"
EMAIL = "riya@example.com"
T0 = 1790316000  # 25 Sep 2026


def _msg(mid, mtype, content, offset=0, private=False, attrs=None, attachments=None):
    return {"id": mid, "message_type": mtype, "content": content, "private": private,
            "created_at": T0 + offset, "content_attributes": attrs or {},
            "attachments": attachments or []}


def _thread():
    first_email = {"email": {"text_content": {
        "full": "My sofa arrived broken.\n\nOn Mon, store wrote:\n> Your order ships today.",
        "quoted": "My sofa arrived broken."}}}
    later_email = {"email": {"text_content": {
        "full": "Order is 1234.\n\n> earlier stuff quoted again",
        "quoted": "Order is 1234."}}}
    return [
        _msg(1, 0, "My sofa arrived broken.\n\nOn Mon, store wrote:\n> Your order ships today.",
             attrs=first_email,
             attachments=[{"data_url": "https://orm.example/blob/photo.jpg"}]),
        _msg(2, 2, "Assigned to DurianAI", 5),                           # activity row
        _msg(3, 1, "internal routing note", 6, private=True),            # private note
        _msg(4, 1, "Sorry! Could you share your order number?", 60,
             attrs={"to_emails": [EMAIL]}),                              # our reply to customer
        _msg(5, 0, "Order is 1234.\n\n> earlier stuff quoted again", 120,
             attrs=json.dumps(later_email)),                             # double-encoded attrs
        _msg(6, 1, "Forwarding the message below…", 180,
             attrs={"to_emails": ["complaints@durian.in"]}),             # earlier team forward
    ]


def test_trail_has_every_email_and_reply_once():
    t = main._email_thread(_thread(), CUSTOMER, EMAIL)
    # first customer email in FULL (its quoted history kept) + attachment link
    assert "My sofa arrived broken." in t and "> Your order ships today." in t
    assert "https://orm.example/blob/photo.jpg" in t
    # our reply to the customer, and the customer's later email (new text only)
    assert "Could you share your order number?" in t
    assert "Order is 1234." in t and "earlier stuff quoted again" not in t
    # noise and our own team forward excluded
    assert "Assigned to DurianAI" not in t and "internal routing note" not in t
    assert "Forwarding the message below" not in t
    # oldest first, labelled with who / when (India time)
    assert t.index("My sofa") < t.index("order number") < t.index("Order is 1234")
    assert f"{CUSTOMER} — 25 Sep 2026" in t and "Team Durian — 25 Sep 2026" in t


def test_html_only_first_email_keeps_quoted_history():
    html_email = {"email": {"html_content": {
        "full": "<div>Please help</div><blockquote>Original order mail<br>Order #99</blockquote>",
        "quoted": "Please help"}}}
    t = main._email_thread([_msg(1, 0, "Please help\n>", attrs=html_email)], CUSTOMER, EMAIL)
    assert "Please help" in t and "Original order mail" in t and "Order #99" in t
    assert "<" not in t.split(":\n", 1)[1]            # tags stripped


def test_trail_is_capped_with_a_note():
    msgs = [_msg(i, 0, "x" * 5000, i) for i in range(20)]
    t = main._email_thread(msgs, CUSTOMER, EMAIL, max_chars=10000)
    assert len(t) < 10100 and t.endswith("see the full conversation in ORM]")


def test_message_fetch_walks_all_pages_and_drops_noise(monkeypatch):
    rows = [_msg(i, [0, 1, 2][i % 3], f"m{i}", i, private=(i == 4)) for i in range(45)]

    async def raw(conv_id, max_pages=10):
        return rows
    monkeypatch.setattr(chatwoot, "get_conversation_messages_raw", raw)
    got = asyncio.run(chatwoot.get_conversation_messages(1))
    assert got[0]["id"] == 0                               # the ORIGINAL email is kept
    assert all(m["message_type"] in (0, 1) and not m["private"] for m in got)
    assert len(got) == 45 - 15 - 1                         # 15 activity rows, 1 private


class _FakeChatwoot:
    """Records outgoing emails; every other Chatwoot call is a harmless no-op."""

    def __init__(self, messages):
        self.messages, self.sent = messages, []

    async def get_conversation_messages(self, conv_id):
        return self.messages

    async def get_conversation(self, conv_id):
        return {"id": conv_id, "custom_attributes": {}, "meta": {"sender": {"email": EMAIL}}}

    async def send_outgoing_message(self, conv_id, content, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return {"id": 99}

    def __getattr__(self, name):
        async def noop(*a, **k):
            return {}
        return noop


def _run_forward(monkeypatch, category):
    fake = _FakeChatwoot(_thread())
    monkeypatch.setattr(main, "chatwoot", fake)
    monkeypatch.setattr(config, "PHASE_2_DRY_RUN", False, raising=False)
    monkeypatch.setattr(main, "_LOCAL_FORWARD_OVERRIDE", "", raising=False)
    rule = {"action": "forward", "forward_to": "team@durian.in", "acknowledge_customer": False,
            "display_name": category}
    asyncio.run(main._phase2_execute_actions(
        1, {"category": category, "action": "forward"}, rule, CUSTOMER, EMAIL,
        "My sofa arrived broken.", "Broken sofa", email_category="legitimate",
        manual=True, complaint_gate_done=True))
    return [m for m in fake.sent if m.get("to_emails") == "team@durian.in"]


def test_manual_forward_sends_the_whole_trail(monkeypatch):
    sent = _run_forward(monkeypatch, "marketing_advertising")
    assert len(sent) == 1
    body = sent[0]["content"]
    assert "My sofa arrived broken." in body and "Order is 1234." in body
    assert "Could you share your order number?" in body


def test_complaint_forward_also_sends_the_whole_trail(monkeypatch):
    sent = _run_forward(monkeypatch, "complaint")
    assert len(sent) == 1 and "Order is 1234." in sent[0]["content"]
