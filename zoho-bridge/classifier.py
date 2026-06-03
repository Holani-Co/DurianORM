# OpenAI-backed team classifier. One LLM call → one team name.
# Falls back to 'support' on any error so a model hiccup never blocks routing.

import json

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


# ── Email-type classifier ────────────────────────────────────────────────
#
# Single LLM call that returns FOUR things at once:
#   1. label             — gmail-style intent (legitimate/promotional/automated/spam)
#   2. confidence        — 1-10 self-rated certainty for the label
#   3. escalation_signal — domain-bucketed risk flag (or "none")
#   4. escalation_reason — short human-readable explanation for the signal
#
# Folding escalation into THIS call (instead of hand-maintaining keyword
# lists or making a second LLM call) is the answer to the "manual regex
# matching, can we use AI?" question. The model already sees the full
# message + sender context; one extra JSON field per response is free.
# Compared to a keyword list it:
#   * catches paraphrases ("I'm thinking of leaving" vs. "resignation")
#   * speaks every language the underlying model speaks
#   * survives typos and emoji
#   * leaves an auditable `escalation_reason` (vs. "matched 'salary'")
# Text normalization (zoho._clean_search_query) stays regex — deterministic,
# free, and AI there would only add latency.

EMAIL_TYPE_VALID = {"legitimate", "promotional", "automated", "spam"}

# Valid escalation buckets — keep this in sync with main._should_create_zoho_ticket.
# 'none' means "ordinary message, no escalation"; anything else triggers a
# Zoho Desk ticket regardless of which team the conversation is routed to.
ESCALATION_SIGNALS_VALID = {
    "none",
    "legal_or_compliance",   # legal notices, GDPR, DMCA, lawsuits, regulator contact
    "hr_sensitive",          # resignation intent, harassment, discrimination, exit
    "financial_dispute",     # refund disputes, chargebacks, fraud, unauthorized charges
    "brand_or_contract",     # paid collabs, sponsorships, partnership/licensing deals
}

EMAIL_TYPE_SYSTEM_PROMPT = """\
You are classifying inbound customer-support messages.

For each message, return FOUR fields:
  1. label             — message intent (one of: legitimate, promotional, automated, spam)
  2. confidence        — integer 1-10, self-rated certainty for `label`
  3. escalation_signal — risk bucket requiring a formal ticket (one of the
                         escalation values listed below; "none" if not applicable)
  4. escalation_reason — short (≤100 chars) plain-English explanation of the
                         escalation_signal (or empty string when "none")

── LABEL definitions ────────────────────────────────────────────────────
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

── CONFIDENCE rubric (be honest, not generous) ──────────────────────────
- 10  = textbook example, would bet money on this
-  8-9 = clear signal, only edge cases would flip the label
-  6-7 = leaning this way but the message has some ambiguity
-  4-5 = could plausibly be 1-2 other labels
-  1-3 = genuinely unclear, defaulting

Default `label` to 'legitimate' when uncertain — false-positives cost real customers.

── ESCALATION_SIGNAL options (pick exactly one) ─────────────────────────
- none                 → DEFAULT. Routine message, no formal ticket needed.
- legal_or_compliance  → Legal notices, regulatory complaints (GDPR / CCPA),
                         copyright / DMCA, lawsuits, attorney contact,
                         government / consumer-protection inquiries, BBB.
- hr_sensitive         → Resignation intent ("I'm leaving", "my last day"),
                         harassment / discrimination claims, exit interviews,
                         complaints about staff conduct, contract disputes
                         relating to employment, severance, non-compete.
- financial_dispute    → Chargebacks, refund disputes with legal threat,
                         "unauthorized charge", fraud claims, stolen-card
                         reports, dispute filed with bank / card issuer.
- brand_or_contract    → Paid sponsorship / collab agreements, brand deals,
                         partnership / licensing contracts, endorsement
                         contracts, large media buys with contract terms.

Set escalation_signal even if label is not "legitimate" (a spam-looking
message can still carry a real legal-threat phrase that warrants attention).
When in doubt, prefer "none" — the keyword equivalent of a false positive
is paging the legal team for a normal refund request.

── OUTPUT ────────────────────────────────────────────────────────────────
Respond as STRICT JSON only, no prose, no markdown, no code fences:
{"label": "...", "confidence": <1-10>, "escalation_signal": "...", "escalation_reason": "..."}
"""


# Fail-safe default that matches the success-shape exactly. Used by every
# error branch so callers never have to None-check fields.
_SAFE_DEFAULT = {
    "label": "legitimate",
    "confidence": 0,
    "escalation_signal": "none",
    "escalation_reason": "",
}


async def classify_email_type(content: str, sender_email: str = "",
                              subject: str = "") -> dict:
    """Classify an inbound message and return a dict with keys:
      - label              (str)  — one of EMAIL_TYPE_VALID
      - confidence         (int)  — 1-10 (0 when classifier failed)
      - escalation_signal  (str)  — one of ESCALATION_SIGNALS_VALID
      - escalation_reason  (str)  — short explanation, '' for 'none'

    Fail-safe: returns _SAFE_DEFAULT on any failure so a classifier outage
    never silently auto-snoozes a real customer or skips escalation.
    """
    if not content or not content.strip():
        return dict(_SAFE_DEFAULT)

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
            max_tokens=200,                  # room for reason field; was 40
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EMAIL_TYPE_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            name="email-type-classification",
            metadata={
                # Track real subject, not inbox name (review feedback).
                "subject_preview": (subject or "")[:80],
                "langfuse_tags": ["classifier", "spam-filter"],
            },
        )
        raw = r.choices[0].message.content.strip()
    except Exception as e:
        print(f"[classifier:email_type] ERROR ({type(e).__name__}): {e} — "
              f"falling back to safe defaults")
        return dict(_SAFE_DEFAULT)

    # Parse JSON defensively; tolerate older models that ignore json mode.
    try:
        parsed = json.loads(raw)
    except Exception:
        # Plain-word response → infer label, keep escalation safe-default.
        word = raw.lower().rstrip(".!?,;:\"'}").strip()
        out = dict(_SAFE_DEFAULT)
        if word in EMAIL_TYPE_VALID:
            out["label"], out["confidence"] = word, 5
        return out

    label = str(parsed.get("label", "")).strip().lower()
    if label not in EMAIL_TYPE_VALID:
        label = "legitimate"

    try:
        conf = int(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(10, conf))

    signal = str(parsed.get("escalation_signal", "none")).strip().lower()
    if signal not in ESCALATION_SIGNALS_VALID:
        signal = "none"

    reason = str(parsed.get("escalation_reason", "") or "").strip()[:200]
    if signal == "none":
        reason = ""   # don't keep stray text on no-escalation rows

    return {
        "label":             label,
        "confidence":        conf,
        "escalation_signal": signal,
        "escalation_reason": reason,
    }
