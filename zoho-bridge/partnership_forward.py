"""Forward a social (Instagram/Facebook) marketing/advertising or collaboration
pitch to the right team — the same intent the EMAIL flow routes by category,
but delivered for a non-email channel: an email (through an email inbox, since a
social inbox can't send mail) carrying the Chatwoot conversation LINK + the full
transcript as a PDF, plus the social conversation assigned to that category's
Chatwoot team so the team continues it in ORM.

Triggered by the social agent's `forward_partnership_enquiry` skill. Reuses the
routing_rules.yaml categories (forward_to / cc / team) so recipients stay in the
client's config, and the review-escalation email pattern (create email contact +
conversation → send). Dark-launched behind PARTNERSHIP_FORWARD_ENABLED.
"""
import os
from datetime import datetime, timezone

import chatwoot
import classifier
import config

# The routing categories this flow handles — advertising/marketing/PR/media and
# brand/influencer/sponsorship collaborations. Others still use plain handoff.
PARTNERSHIP_CATEGORIES = ("marketing_advertising", "collaboration_request")


def _lat(s) -> str:
    """fpdf2 core fonts are latin-1; replace glyphs they can't render so a name
    or emoji never crashes the PDF (fine for English B2B pitches)."""
    return str(s or "").encode("latin-1", "replace").decode("latin-1")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _rule_for(category: str) -> dict:
    cats = (classifier.get_routing_rules() or {}).get("categories") or {}
    return cats.get(category) or {}


def _conv_link(conv_id: int) -> str:
    base = (config.CHATWOOT_PUBLIC_URL or "").rstrip("/")
    return f"{base}/app/accounts/{config.CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}"


def _sender_name(conv: dict) -> str:
    return ((conv.get("meta") or {}).get("sender") or {}).get("name") or ""


def _fmt_ts(epoch) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return ""


def _transcript_rows(messages: list) -> list[tuple[str, str, str]]:
    rows = []
    for m in messages or []:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        who = "Customer" if m.get("message_type") in (0, "incoming") else "Durian"
        rows.append((who, _fmt_ts(m.get("created_at")), content))
    return rows


def build_pdf(conv: dict, channel: str, category_display: str, messages: list) -> bytes:
    """A clean one-file transcript PDF: header (channel / enquiry type / customer)
    then each message as 'who · time' + body."""
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def line(text, size=10, bold=False):
        # new_x/new_y reset x to the left margin so the next width-0 multi_cell
        # has the full page width (default leaves x at the right edge).
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.multi_cell(0, 6, _lat(text), new_x="LMARGIN", new_y="NEXT")

    line("Durian — Conversation Transcript", size=15, bold=True)
    pdf.ln(1)
    for label, val in (("Channel", channel.title()),
                       ("Enquiry type", category_display),
                       ("Customer", _sender_name(conv) or "(unknown)")):
        line(f"{label}: {val}")
    pdf.ln(3)
    rows = _transcript_rows(messages)
    if not rows:
        line("(no messages in this conversation yet)")
    for who, ts, content in rows:
        line(f"{who}  -  {ts}" if ts else who, bold=True)
        line(content)
        pdf.ln(2)
    return bytes(pdf.output())


async def run(conv_id: int, conv: dict, category: str, channel: str) -> dict:
    """Do the forward. Idempotent (skips once `partnership_forwarded` is set).
    Returns {forwarded: True, team, recipient, display} on success, else
    {forwarded: False, reason}."""
    if not config.PARTNERSHIP_FORWARD_ENABLED:
        return {"forwarded": False, "reason": "disabled"}
    if category not in PARTNERSHIP_CATEGORIES:
        return {"forwarded": False, "reason": "category_not_forwardable"}
    if (conv.get("custom_attributes") or {}).get("partnership_forwarded"):
        return {"forwarded": False, "reason": "already_forwarded"}

    rule = _rule_for(category)
    forward_to = (rule.get("forward_to") or "").strip()
    if not config.PARTNERSHIP_FORWARD_INBOX_ID or not forward_to:
        return {"forwarded": False, "reason": "not_configured"}
    display = rule.get("display_name") or category
    cc = ", ".join(rule.get("cc") or [])
    # LOCAL TEST: redirect to one inbox so a local run never emails a real team.
    override = os.environ.get("LOCAL_FORWARD_OVERRIDE", "").strip()
    if override:
        forward_to, cc = override, ""

    name = _sender_name(conv)
    link = _conv_link(conv_id)
    try:
        messages = await chatwoot.get_conversation_messages(conv_id)
    except Exception:
        messages = []
    pdf = build_pdf(conv, channel, display, messages)

    subject = f"{display} from {channel.title()} - {name or 'Durian customer'}"
    body = "\n".join([
        f"Forwarding a {display.lower()} received on {channel.title()} "
        f"from {name or 'a customer'}.", "",
        "You can view and continue the conversation in ORM here:",
        link, "",
        "The full conversation so far is attached as a PDF.", "",
        "Regards,", "Team Durian",
    ])

    # Send through the email inbox (a social inbox can't email) — same pattern as
    # the low-star review escalation.
    contact_id, _source = await chatwoot.create_contact(
        name="Durian ORM Forwarding",
        identifier="durian-orm-partnership-forward",
        inbox_id=config.PARTNERSHIP_FORWARD_INBOX_ID,
        email="orm-forwarding@durian.in")
    # A unique source_id per social conversation so each forward is its own email
    # thread (not all threaded onto the shared forwarding contact).
    fwd_conv = await chatwoot.create_conversation(
        source_id=f"pitchfwd_{conv_id}",
        inbox_id=config.PARTNERSHIP_FORWARD_INBOX_ID, contact_id=contact_id,
        additional_attributes={"mail_subject": subject})
    await chatwoot.send_email_with_attachment(
        fwd_conv, body, to_emails=forward_to,
        filename=f"durian-conversation-{conv_id}.pdf", data=pdf,
        ctype="application/pdf", cc_emails=cc or None)

    # Assign the ORIGINAL social conversation to the category's Chatwoot team.
    assigned_team = None
    team_name = (rule.get("team") or "").strip()
    if team_name:
        try:
            tid = await chatwoot.find_team_id(team_name)
            if tid:
                await chatwoot.assign_team(conv_id, tid)
                assigned_team = team_name
            else:
                print(f"[partnership-forward] no Chatwoot team named '{team_name}'")
        except Exception as e:
            print(f"[partnership-forward] team assign failed for conv {conv_id}: {e}")

    # Idempotency flag + an audit note on the social conversation.
    try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "partnership_forwarded": category,
            "partnership_forwarded_at": _now_iso()})
    except Exception as e:
        print(f"[partnership-forward] flag merge failed for conv {conv_id}: {e}")
    try:
        cc_bit = f" (cc {cc})" if cc else ""
        await chatwoot.post_private_note(
            conv_id,
            f"📨 {display} forwarded to {assigned_team or 'the team'} at "
            f"{forward_to}{cc_bit} — ORM conversation link + full transcript PDF "
            f"emailed. Conversation assigned to the {assigned_team or 'relevant'} team.")
    except Exception as e:
        print(f"[partnership-forward] note failed for conv {conv_id}: {e}")

    return {"forwarded": True, "team": assigned_team, "recipient": forward_to,
            "display": display}
