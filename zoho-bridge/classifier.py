# OpenAI-backed team classifier. One LLM call → one team name.
# Falls back to 'support' on any error so a model hiccup never blocks routing.

import config
from llm_client import client

VALID_TEAMS = {"legal", "marketing", "hr", "support"}

SYSTEM_PROMPT = """\
You are a routing classifier for IComics / kisnemanga (a manga & comics store).
Read the customer's first message and assign it to EXACTLY ONE team.

Teams:
- legal     → legal notices, copyright/DMCA, takedowns, lawsuits, contracts, IP, GDPR/privacy
- marketing → collaborations, sponsorships, influencer/PR inquiries, brand partnerships
- hr        → job applications, recruitment, internships, complaints about staff conduct
- support   → DEFAULT. Orders, products, returns, shipping, refunds, general questions, anything else

Respond with EXACTLY ONE word, lowercase: legal | marketing | hr | support
No explanation. No punctuation. Just the word.
"""


async def classify(message_content: str, inbox_name: str = "") -> str:
    """Return one of: legal, marketing, hr, support. Always returns a valid value."""
    if not message_content or not message_content.strip():
        return "support"

    user_msg = f"[Inbox: {inbox_name}]\n{message_content}" if inbox_name else message_content

    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            max_tokens=5,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            name="team-classification",
            metadata={
                "inbox": inbox_name or "unknown",
                "langfuse_tags": ["classifier"],
            },
        )
        raw = r.choices[0].message.content.strip().lower()
        # Strip trailing punctuation just in case
        raw = raw.rstrip(".!?,;:")
        return raw if raw in VALID_TEAMS else "support"
    except Exception as e:
        print(f"[classifier] ERROR ({type(e).__name__}): {e} — falling back to 'support'")
        return "support"


# ── Email-type classifier (Gmail-style intent: legitimate / promotional /
#    automated / spam). Returns (label, confidence 1-10). Confidence lets
#    the caller distinguish auto-snooze-worthy spam from borderline cases.
EMAIL_TYPE_VALID = {"legitimate", "promotional", "automated", "spam"}

EMAIL_TYPE_SYSTEM_PROMPT = """\
You are classifying inbound customer-support messages by intent type and
returning a confidence score.

Read the message and assign EXACTLY ONE label PLUS a confidence integer 1-10.

Labels:
- legitimate   → DEFAULT. A real human asking a question, raising an issue,
                 making a request, or having a conversation. Examples: order
                 questions, refund requests, complaints, feedback, collab
                 inquiries from a real person, legal notices, job applications.
- promotional  → Marketing / sales emails. Newsletters, offers, "limited-time
                 discount", "Black Friday sale", template influencer pitches,
                 unsolicited sales outreach, bulk PR blasts.
- automated    → Machine-generated notifications: shipping confirmations,
                 receipts / invoices, OTP codes, "Your order has shipped",
                 security alerts, calendar invites, system bounces, no-reply
                 system mail.
- spam         → Phishing, scams, "Nigerian prince" / inheritance, crypto
                 pumps, obvious bulk junk, fake "act now or lose access"
                 pressure. If in doubt between promotional and spam, choose
                 promotional — only label spam with HIGH CONFIDENCE (≥8).

Confidence rubric (be honest, not generous):
- 10  = textbook example, would bet money on this
-  8-9 = clear signal, only edge cases would flip the label
-  6-7 = leaning this way but the message has some ambiguity
-  4-5 = could plausibly be 1-2 other labels
-  1-3 = genuinely unclear, defaulting

Default to 'legitimate' when uncertain — false-positives cost real customers.

Respond as STRICT JSON only, no prose, no markdown, no code fences:
{"label": "<one of the four labels>", "confidence": <1-10 integer>}
"""


async def classify_email_type(content: str, sender_email: str = "",
                              subject: str = "") -> tuple[str, int]:
    """Classify an inbound message by intent type and return (label,
    confidence). Fail-safe: returns ('legitimate', 0) on any failure so a
    classifier outage never silently auto-snoozes a real customer."""
    if not content or not content.strip():
        return "legitimate", 0

    ctx_parts = []
    if subject:
        ctx_parts.append(f"Subject: {subject}")
    if sender_email:
        ctx_parts.append(f"From: {sender_email}")
    ctx_parts.append(f"Body:\n{content}")
    user_msg = "\n".join(ctx_parts)

    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            max_tokens=40,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EMAIL_TYPE_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            name="email-type-classification",
            metadata={
                "inbox": subject or "unknown",
                "langfuse_tags": ["classifier", "spam-filter"],
            },
        )
        raw = r.choices[0].message.content.strip()
    except Exception as e:
        print(f"[classifier:email_type] ERROR ({type(e).__name__}): {e} — "
              f"falling back to ('legitimate', 0)")
        return "legitimate", 0

    # Parse JSON defensively; tolerate older models that ignore json mode.
    import json as _json
    try:
        parsed = _json.loads(raw)
    except Exception:
        word = raw.lower().rstrip(".!?,;:\"'}").strip()
        if word in EMAIL_TYPE_VALID:
            return word, 5
        return "legitimate", 0

    label = str(parsed.get("label", "")).strip().lower()
    if label not in EMAIL_TYPE_VALID:
        return "legitimate", 0
    try:
        conf = int(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(10, conf))
    return label, conf
