# OpenAI-backed team classifier. One LLM call → one team name.
# Falls back to 'support' on any error so a model hiccup never blocks routing.

import json

import config
from llm_client import client

VALID_TEAMS = {"legal", "marketing", "hr", "support"}

# Map an email-type classifier escalation_signal → team_key.
# When the email-type classifier (classify_email_type) returns a non-"none"
# escalation_signal, this mapping is the SOURCE OF TRUTH for routing — the
# general `classify()` LLM call is skipped entirely. Rationale documented at
# the use-site in main.handle_message_created; short version: keeps the
# routing team and the Zoho-ticket team consistent (incident: a copyright
# legal notice was correctly flagged as legal_or_compliance for ticket
# escalation but the standalone team classifier misrouted it to HR because
# the body mentioned "professional models" and "personality rights").
#
# Keep keys in lockstep with classifier.ESCALATION_SIGNALS_VALID. If a new
# signal is ever added there without an entry here, signal-based routing
# silently falls back to the general classifier for that signal — not
# wrong, but loses the consistency benefit.
ESCALATION_SIGNAL_TEAM = {
    "legal_or_compliance": "legal",
    "hr_sensitive":        "hr",
    # Financial disputes (chargebacks, refund battles) are handled by
    # support today — there's no dedicated finance team. Re-map here when
    # one is added without touching main.py.
    "financial_dispute":   "support",
    "brand_or_contract":   "marketing",
}

# Tightened team-routing prompt. Used ONLY as fallback when the email-type
# classifier returned escalation_signal="none" (i.e. the message has no
# strong domain signal). The previous shorter prompt occasionally tripped
# on legal notices that mentioned "models" / "staff" / "complaints" and
# misrouted them to HR — fixed by:
#   1. Explicitly listing the strong legal markers (Act/Section citations,
#      "demand for compensation", advocate/law-firm sender).
#   2. Disambiguating that HR only covers OUR staff/employees — a legal
#      notice that mentions third-party models in copyright context is
#      NOT HR.
#   3. Adding 2 worked examples (one legal, one HR) so the model sees the
#      distinction concretely.
SYSTEM_PROMPT = """\
You are a routing classifier for IComics / kisnemanga (a manga & comics store).
Read the customer's first message and assign it to EXACTLY ONE team.

Teams:
- legal     → ANY communication from a lawyer / attorney / advocate / law firm.
              Legal notices, demand letters, cease-and-desist, copyright /
              DMCA / IP infringement claims, lawsuits, GDPR / privacy /
              data-protection complaints, regulator contact, takedowns,
              contract disputes. KEY MARKERS: citations of legal Acts or
              Sections, "demand for compensation/damages", "without prejudice",
              advocate signature, law-firm letterhead, threats of legal action.
              ⚠️  If a legal notice mentions models, employees, contributors
              or "personality rights" in the context of copyright or
              infringement, it is STILL legal, not HR.
- marketing → Genuine business outreach: paid collaborations, sponsorships,
              influencer / PR inquiries, brand partnerships, press queries.
- hr        → JOB APPLICATIONS to work at our company, recruitment, internship
              requests, complaints SPECIFICALLY about OUR staff / employees /
              agents. ⚠️  This is for employment-style matters only — NOT
              for any message that happens to mention people, models, or
              "complaints" in a non-employment context.
- support   → DEFAULT. Orders, products, returns, shipping, refunds, general
              questions, anything else that isn't clearly one of the above.

Examples:
- "We act on behalf of ImagesBazaar... Section 51 of the Copyright Act 1957
  was violated... demand payment of ₹6,36,000 within 7 days." → legal
- "Hi, I'd like to apply for the SEO Manager role advertised on your site." → hr

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


# ── Structured-output schema (OpenAI strict json_schema mode) ─────────────
#
# Previously this function used `response_format={"type": "json_object"}`,
# which guarantees the response is valid JSON but does NOT constrain its
# shape — so we hand-validated every field downstream (lowercase the label,
# enum-check it, int-coerce + clamp the confidence, enum-check the signal,
# etc.). About 20 lines of defensive code.
#
# Strict json_schema mode (gpt-4o / gpt-4o-mini 2024-08-06+) enforces the
# schema at generation time: the model literally cannot return a response
# that violates the schema — OpenAI re-samples internally until it matches.
# That lets us delete all the field-by-field validation.
#
# Strict-mode caveats (worth knowing if extending the schema):
#   * Every property in `properties` MUST appear in `required` — no optional
#     fields. (Workaround: include the field but allow it to be empty.)
#   * `additionalProperties: false` is REQUIRED at every object level.
#   * Numeric `minimum`/`maximum` and string `minLength`/`maxLength`/`pattern`
#     are NOT supported. For bounded integers we use `enum: [1..10]`; for
#     string length we truncate after the call (see escalation_reason below).
#   * `enum`, `type`, `items`, `$ref`, `anyOf` ARE supported.
EMAIL_TYPE_RESPONSE_SCHEMA = {
    "name": "email_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "label", "confidence", "escalation_signal", "escalation_reason",
        ],
        "properties": {
            "label": {
                "type": "string",
                "enum": ["legitimate", "promotional", "automated", "spam"],
            },
            "confidence": {
                # Strict mode doesn't support minimum/maximum, so enumerate.
                "type": "integer",
                "enum": list(range(1, 11)),
            },
            "escalation_signal": {
                "type": "string",
                "enum": [
                    "none",
                    "legal_or_compliance",
                    "hr_sensitive",
                    "financial_dispute",
                    "brand_or_contract",
                ],
            },
            "escalation_reason": {
                # Cannot constrain maxLength in strict mode; truncated after
                # the call. Always required (empty string when signal=none).
                "type": "string",
            },
        },
    },
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
            response_format={
                "type": "json_schema",
                "json_schema": EMAIL_TYPE_RESPONSE_SCHEMA,
            },
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
        raw = r.choices[0].message.content
    except Exception as e:
        print(f"[classifier:email_type] ERROR ({type(e).__name__}): {e} — "
              f"falling back to safe defaults")
        return dict(_SAFE_DEFAULT)

    # Strict json_schema mode guarantees the shape — every field is present,
    # types are correct, and string fields hit their enum. The previous
    # ~20 lines of defensive validation (lowercase, enum-check, int-clamp)
    # are no longer needed: OpenAI re-samples internally until the model
    # produces a response that matches EMAIL_TYPE_RESPONSE_SCHEMA.
    #
    # We still json.loads inside try/except because strict mode CAN return
    # an empty `content` field in rare failure modes — model refusal,
    # content filtering, max_tokens cut-off mid-generation. In those cases
    # we fall through to the safe default rather than crashing on a
    # malformed/empty payload.
    try:
        parsed = json.loads(raw or "")
    except Exception as e:
        print(f"[classifier:email_type] strict-schema response unparseable "
              f"({type(e).__name__}: {e}) — content was {raw!r}; "
              f"falling back to safe defaults")
        return dict(_SAFE_DEFAULT)

    # `maxLength` isn't supported in strict mode, so truncate the reason
    # here for storage sanity (200 chars matches what classify_email_type's
    # docstring & downstream consumers expect).
    reason = (parsed.get("escalation_reason") or "")[:200]

    # When the model picked signal=none, drop any stray reason text so the
    # audit row stays clean. (Schema requires the field to exist; this just
    # normalises its content for the no-escalation case.)
    if parsed["escalation_signal"] == "none":
        reason = ""

    return {
        "label":             parsed["label"],
        "confidence":        parsed["confidence"],
        "escalation_signal": parsed["escalation_signal"],
        "escalation_reason": reason,
    }


# ─── 12-category email classifier (Durian: hello@durian.in) ───────────────
#
# Companion to classify_email_type. Where classify_email_type answers
# "is this spam / does it need escalation?", classify_email_category answers
# the next product question: "which of the 12 routing categories does it
# fall into?". Categories + routing rules + few-shot examples are stored
# in routing_rules.yaml so non-engineers can tune them without a code
# change; classifier.py reads the YAML at import time.
#
# Phase 1 (this PR): the function is wired into main.handle_message_created
# AFTER existing routing happens. It records its decision in conversation
# custom_attributes (email_category_v2) and a private note, but does NOT
# act on it — no forwarding, no acknowledgments. The intent is to observe
# classification accuracy on real traffic for a week before flipping any
# behavioural switches.

import os
from pathlib import Path

try:
    import yaml as _yaml
except ImportError:                                 # pragma: no cover
    _yaml = None

_ROUTING_PATH = Path(os.getenv(
    "DURIAN_ROUTING_RULES_PATH",
    str(Path(__file__).parent / "routing_rules.yaml"),
))


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base. Override values win;
    nested dicts are merged key-by-key (not replaced wholesale). Lists are
    replaced wholesale — partial-list merging is too ambiguous to be safe."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_routing_rules() -> dict:
    """Read routing_rules.yaml + optional routing_rules.local.yaml. The base
    file is the committed source of truth. The .local file is gitignored
    and used to redirect forward destinations during local testing without
    editing the prod config; it's merged on top of the base.

    Returns {} on a missing/broken BASE file so the categorizer silently
    no-ops rather than crashing the webhook path. A missing or broken
    .local file is reported but never fatal — the base still wins."""
    if _yaml is None:
        print("[classifier:category] PyYAML not installed — categorizer disabled")
        return {}
    try:
        with open(_ROUTING_PATH, "r", encoding="utf-8") as f:
            base = _yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"[classifier:category] routing rules not found at "
              f"{_ROUTING_PATH} — categorizer disabled")
        return {}
    except Exception as e:
        print(f"[classifier:category] failed to parse routing rules: "
              f"{type(e).__name__}: {e} — categorizer disabled")
        return {}

    # Two override layers, applied in order on top of the base:
    #   1. routing_rules.local.yaml — gitignored, for local dev (matches
    #      the .env / .env.local convention).
    #   2. Whatever file the DURIAN_ROUTING_OVERRIDE_PATH env var points
    #      to (e.g. routing_rules.prod-test.yaml on the VM during the
    #      client's prod testing phase). Committed, so the override
    #      ships with the bridge and is activated by a one-line env
    #      change on the VM.
    # Either layer can be absent — base wins by default. Both are
    # non-fatal on parse errors; we keep going with what we have.
    merged = base
    for label, path in (
        ("local",        _ROUTING_PATH.with_name("routing_rules.local.yaml")),
        ("env-override", Path(os.getenv("DURIAN_ROUTING_OVERRIDE_PATH", "") or "/__nonexistent__")),
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                layer = _yaml.safe_load(f) or {}
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"[classifier:category] failed to parse {path.name} ({label}): "
                  f"{type(e).__name__}: {e} — skipping this layer")
            continue
        merged = _deep_merge(merged, layer)
        print(f"[classifier:category] merged routing override from "
              f"{path.name} ({label})")
    return merged


_ROUTING_RULES = _load_routing_rules()
_CATEGORY_KEYS = list((_ROUTING_RULES.get("categories") or {}).keys())
_CONFIDENCE_THRESHOLD = float(_ROUTING_RULES.get("confidence_threshold", 0.6))


def category_choices() -> list[dict]:
    """[{category, display_name}] for every routing category — used to build
    the dropdown on the human-in-the-loop Category decision card."""
    cats = _ROUTING_RULES.get("categories") or {}
    return [
        {"category": key,
         "display_name": (cfg or {}).get("display_name") or key.replace("_", " ").title()}
        for key, cfg in cats.items()
    ]


def category_display_name(category: str) -> str:
    """Human label for a category key (falls back to a title-cased key)."""
    cfg = (_ROUTING_RULES.get("categories") or {}).get(category) or {}
    return cfg.get("display_name") or (category or "").replace("_", " ").title()


def _build_category_system_prompt(rules: dict) -> str:
    """Compose the LLM system prompt from the YAML. The category descriptions
    + few-shot examples live in the YAML so non-engineers can edit them; we
    just lay them out into the prompt at startup."""
    cats = rules.get("categories") or {}
    lines = [
        "You are an email-routing classifier for Durian Industries — a "
        "furniture brand whose customer-support inbox (hello@durian.in) "
        "receives a mix of customer questions, complaints, business "
        "enquiries, and outreach. Read the email's subject and body and "
        "assign it to EXACTLY ONE of the categories below.",
        "",
        "Categories:",
        "",
    ]
    for key, cfg in cats.items():
        desc = (cfg.get("description") or "").strip()
        lines.append(f"- {key} ({cfg.get('display_name', key)})")
        for descline in desc.splitlines():
            lines.append(f"    {descline.strip()}")
        examples = cfg.get("examples") or []
        if examples:
            lines.append("  Example messages:")
            for ex in examples[:4]:
                lines.append(f'    • "{ex}"')
        # Subject-line keyword anchors from the client's Email-Keywords
        # sheet — short topic phrases that strongly indicate the category,
        # surfaced to the model as an extra signal on top of the prose
        # description + example messages. The cap (CATEGORY_KEYWORDS_IN_PROMPT,
        # default 200) is high enough to include the full client list so the
        # model weighs all of them when scoring confidence.
        keywords = cfg.get("keywords") or []
        if keywords:
            shown = ", ".join(str(k) for k in keywords[:config.CATEGORY_KEYWORDS_IN_PROMPT])
            lines.append(f"  Common subject keywords: {shown}")
        lines.append("")

    lines.extend([
        "Rules:",
        "  • Pick the category that BEST fits the customer's primary "
        "intent. If a message touches multiple categories, pick the most "
        "actionable one (complaint > enquiry; legal_complaint > complaint).",
        "  • Distinguish carefully: 'product_enquiry' is PRE-purchase "
        "interest; 'existing_order_enquiry' is POST-purchase status; "
        "'complaint' is dissatisfaction; 'legal_complaint' is when the "
        "customer cites law / threatens proceedings.",
        "  • Distinguish 'franchise_dealership' (wants to sell Durian) "
        "from 'vendor_supplier_enquiry' (wants to sell TO Durian).",
        "  • Distinguish 'marketing_advertising' (paid services pitch) "
        "from 'collaboration_request' (brand/influencer barter / co-marketing).",
        "  • Output a confidence score 0.0-1.0 reflecting how cleanly the "
        "message matches your chosen category. Use < 0.6 when uncertain.",
        "  • Brief reason: one sentence, what signal led you to the "
        "category.",
        "  • alternatives: the 2 NEXT most likely categories (after your top "
        "pick), each with its own 0.0-1.0 confidence, most-likely first. These "
        "help a human pick the right one when your top confidence is low. "
        "Omit categories that clearly don't apply — fewer is fine.",
    ])
    return "\n".join(lines)


_CATEGORY_SYSTEM_PROMPT = (
    _build_category_system_prompt(_ROUTING_RULES) if _ROUTING_RULES else ""
)

_CATEGORY_RESPONSE_SCHEMA = {
    "name":   "email_category_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category": {
                "type": "string",
                "enum": _CATEGORY_KEYS or ["fallback"],
                "description": "Which of the 12 routing categories this email belongs to.",
            },
            "confidence": {
                "type":        "number",
                "description": "0.0–1.0 confidence. Below 0.6 → treated as uncategorised.",
            },
            "reason": {
                "type":        "string",
                "description": "One short sentence: which signal in the email drove the choice.",
            },
            "alternatives": {
                "type": "array",
                "description": "Up to 2 next-most-likely categories, most likely first.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "category":   {"type": "string", "enum": _CATEGORY_KEYS or ["fallback"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["category", "confidence"],
                },
            },
        },
        "required": ["category", "confidence", "reason", "alternatives"],
    },
}


_SAFE_CATEGORY_DEFAULT = {
    "category":    "fallback",
    "confidence":  0.0,
    "reason":      "",
    "action":      "in_channel",
    "rule":        None,
}


async def classify_email_category(content: str, sender_email: str = "",
                                  subject: str = "") -> dict:
    """Classify an inbound email into one of the 12 Durian routing
    categories. Returns a dict with:
      category    – one of the keys in routing_rules.yaml (or 'fallback')
      confidence  – 0.0–1.0
      reason      – short LLM-produced explanation
      action      – 'in_channel' or 'forward' (looked up from the YAML)
      rule        – the full routing-rule dict for the chosen category
                    (or None for 'fallback'); main.py uses this in Phase 2
                    to know where to forward, who to CC, etc.

    Fail-safe: returns _SAFE_CATEGORY_DEFAULT on any failure — empty
    input, missing config, LLM error, unparseable response. The categorizer
    can ALWAYS no-op without affecting other routing logic.
    """
    if not content or not content.strip() or not _CATEGORY_KEYS:
        return dict(_SAFE_CATEGORY_DEFAULT)

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
            max_tokens=200,
            response_format={
                "type":        "json_schema",
                "json_schema": _CATEGORY_RESPONSE_SCHEMA,
            },
            messages=[
                {"role": "system", "content": _CATEGORY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            name="email-12-category-classification",
            metadata={
                "subject_preview": (subject or "")[:80],
                "langfuse_tags":   ["classifier", "category-v2"],
            },
        )
        raw = r.choices[0].message.content
    except Exception as e:
        print(f"[classifier:category] ERROR ({type(e).__name__}): {e} — "
              f"falling back to fallback category")
        return dict(_SAFE_CATEGORY_DEFAULT)

    try:
        parsed = json.loads(raw or "")
    except Exception as e:
        print(f"[classifier:category] unparseable response "
              f"({type(e).__name__}: {e}); content was {raw!r}")
        return dict(_SAFE_CATEGORY_DEFAULT)

    cat        = parsed.get("category") or "fallback"
    confidence = float(parsed.get("confidence") or 0)
    reason     = (parsed.get("reason") or "")[:200]
    # Top alternatives the LLM ranked below its pick — kept valid + deduped so
    # the human-in-the-loop card can show "other likely categories".
    alternatives = [
        {"category": a.get("category"), "confidence": float(a.get("confidence") or 0)}
        for a in (parsed.get("alternatives") or [])
        if a.get("category") in _CATEGORY_KEYS and a.get("category") != cat
    ][:3]

    # Below threshold → treat as fallback. Phase 1 logs both the
    # original LLM pick AND the resolved category so we can later
    # tune the threshold from observed accuracy.
    if confidence < _CONFIDENCE_THRESHOLD or cat not in _CATEGORY_KEYS:
        rule_key = "fallback"
    else:
        rule_key = cat

    if rule_key == "fallback":
        fallback_cfg = _ROUTING_RULES.get("fallback") or {}
        return {
            "category":   "fallback",
            "confidence": confidence,
            "reason":     reason,
            "action":     fallback_cfg.get("action", "in_channel"),
            "rule":       None,
            # Keep the raw LLM pick for auditing — useful for tuning the
            # threshold later without re-running the LLM on archived
            # messages.
            "raw_category": cat,
            "alternatives": alternatives,
        }

    rule = (_ROUTING_RULES["categories"] or {}).get(rule_key) or {}
    result = {
        "category":   rule_key,
        "confidence": confidence,
        "reason":     reason,
        "action":     rule.get("action", "in_channel"),
        "rule":       rule,
        "alternatives": alternatives,
    }
    # Bulk orders split into government vs private buyers → resolve the sector
    # and point the forward at that sector's handler. main.py decides whether
    # to auto-forward or ask an agent (sector decision card) based on
    # sector_confidence. The rule is COPIED before overriding forward_to/cc so
    # the shared _ROUTING_RULES dict is never mutated.
    if rule_key == "project_bulk_order" and rule.get("sector_routing"):
        sec = await classify_bulk_sector(content, sender_email, subject)
        sroute = rule["sector_routing"].get(sec["sector"]) or {}
        result["sector"]            = sec["sector"]
        result["sector_confidence"] = sec["confidence"]
        result["sector_reason"]     = sec["reason"]
        result["rule"] = {
            **rule,
            "forward_to": sroute.get("forward_to") or rule.get("forward_to"),
            "cc":         sroute.get("cc") or rule.get("cc") or [],
        }
    return result


# ── Bulk-order sector sub-classifier (government vs private buyer) ─────────
_BULK_SECTOR_SCHEMA = {
    "name":   "bulk_order_sector",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sector":     {"type": "string", "enum": ["government", "private"]},
            "confidence": {"type": "number"},
            "reason":     {"type": "string"},
        },
        "required": ["sector", "confidence", "reason"],
    },
}


def _build_bulk_sector_prompt(sector_routing: dict) -> str:
    gov_kw = ", ".join(str(k) for k in (sector_routing.get("government", {}).get("keywords") or []))
    pri_kw = ", ".join(str(k) for k in (sector_routing.get("private", {}).get("keywords") or []))
    return (
        "A bulk / project furniture order has come in. Decide whether the BUYER "
        "is a GOVERNMENT / public-sector body or a PRIVATE company, so it routes "
        "to the right handler.\n\n"
        "Strongest signal: the sender's email domain. A .gov.in or .nic.in domain "
        "is government almost without exception. A company/brand domain or a free "
        "mailbox (gmail/outlook) leans private — but weigh the organisation name "
        "too.\n\n"
        f"Government / public-sector name signals: {gov_kw}\n\n"
        f"Private-sector name signals: {pri_kw}\n\n"
        "Ambiguous (judge from the specific name): Trust/Foundation/Society/NGO, "
        "Co-operative/Sahakari, University/College (.ac.in can be public or "
        "private), Bank (public vs private like HDFC/ICICI/Axis), and a bare "
        "'Corporation'/'Limited'. A private company CAN run a formal tender, so "
        "the org name tells you WHO they are; the process (tender/GeM/EMD) does "
        "not.\n\n"
        "Output: sector (government|private), a 0.0-1.0 confidence (use < 0.8 "
        "when genuinely unsure), and a one-sentence reason naming the signal."
    )


_BULK_SECTOR_PROMPT = _build_bulk_sector_prompt(
    ((_ROUTING_RULES.get("categories") or {}).get("project_bulk_order") or {}).get("sector_routing") or {}
) if _ROUTING_RULES else ""


async def classify_bulk_sector(content: str, sender_email: str = "",
                               subject: str = "") -> dict:
    """For a project_bulk_order email, decide government vs private buyer.
    Returns {sector, confidence, reason}. A government email domain is a hard
    signal (0.99). Fail-safe: returns a 0-confidence 'private' default on any
    error so routing never blocks."""
    default = {"sector": "private", "confidence": 0.0, "reason": ""}

    # Hard signal: a government email domain wins outright.
    domain = (sender_email.rsplit("@", 1)[-1] if "@" in sender_email else "").lower()
    if domain.endswith(".gov.in") or domain.endswith(".nic.in"):
        return {"sector": "government", "confidence": 0.99,
                "reason": f"Sender domain {domain} is a government domain."}

    ctx = []
    if subject:
        ctx.append(f"Subject: {subject}")
    if sender_email:
        ctx.append(f"From: {sender_email}")
    ctx.append(f"Body:\n{content}")
    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_schema", "json_schema": _BULK_SECTOR_SCHEMA},
            messages=[
                {"role": "system", "content": _BULK_SECTOR_PROMPT},
                {"role": "user",   "content": "\n".join(ctx)},
            ],
            name="bulk-order-sector-classification",
            metadata={"langfuse_tags": ["classifier", "bulk-sector"]},
        )
        parsed = json.loads(r.choices[0].message.content or "")
    except Exception as e:
        print(f"[classifier:bulk-sector] ERROR ({type(e).__name__}): {e}")
        return default

    sector = parsed.get("sector")
    if sector not in ("government", "private"):
        return default
    return {"sector":     sector,
            "confidence": float(parsed.get("confidence") or 0),
            "reason":     (parsed.get("reason") or "")[:200]}
