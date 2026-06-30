# Template-driven AI reply drafter, Durian voice, channel-aware.
#
# One drafter for every channel — picks + lightly personalises an APPROVED
# Durian template (canned response) for the given channel.
#
# Returns (reply_text, action) where action ∈ {"auto", "handoff"}.
#   - "auto"    → safe to post automatically (positive / simple).
#   - "handoff" → needs a human (complaint, low rating, anything risky).
#
# Templates live in Chatwoot as canned responses with short_codes like
# `<channel>_<category>` (review_positive_5star, whatsapp_negative_info_needed,
# instagram_acknowledge_feedback, …). The team edits them from the UI; we
# fetch them live here so a UI edit changes the AI's drafts with no code
# change. The model's only job is to PICK the best-fit template and lightly
# personalise it (greeting + one specific reference) — never to invent new
# wording.

import config
import chatwoot
from llm_client import client

# Display labels + warnings the system prompt weaves into the channel-specific
# instructions. Keep these short; the model adapts tone from the templates.
CHANNEL_LABELS = {
    "review":    "a Google review of one of our showrooms",
    "whatsapp":  "WhatsApp",
    "instagram": "Instagram",
    "facebook":  "Facebook Messenger",
}

CHANNEL_WARNINGS = {
    "review":    "This reply is PUBLIC on Google — be extra careful. Never "
                 "quote prices, promise refunds/replacements, or admit fault.",
    "whatsapp":  "This reply is a private 1-to-1 WhatsApp message.",
    "instagram": "This reply is a private Instagram DM.",
    "facebook":  "This reply is a private Facebook Messenger message.",
}


SYSTEM_PROMPT_FMT = """\
You are the brand voice of Durian, an Indian premium furniture retailer.
You are writing a reply to a customer on {channel_label}.

{channel_warning}

You are given a set of APPROVED reply templates (each with a short_code) and
one customer message. Your job:

1. PICK the single template that best fits the message's sentiment and content.
2. PERSONALISE it lightly:
   - Replace "Dear Customer" with the sender's first name if one is given
     (e.g. "Dear Rajiv,"). If no real name, keep "Dear Customer,".
   - You MAY weave in ONE short, specific reference to what they mentioned,
     only where it reads naturally. Keep the template's structure and wording
     otherwise intact.
3. Do NOT invent new promises, prices, refunds, or claims. Do NOT add anything
   the chosen template doesn't already say beyond the light touches above.

── AUTO vs HANDOFF ────────────────────────────────────────────────────────
"action" decides whether this reply is safe to send WITHOUT a human. The
deciding question is simply: does the text contain ANY criticism or complaint?
- "auto"    → the message expresses satisfaction (praise, thanks, a happy
              experience) and contains NO complaint or criticism. Brief or mild
              praise still counts — e.g. "good furniture, happy with my
              purchase" or "nice showroom, satisfied" are "auto".
- "handoff" → the text contains ANY complaint or criticism: a mention of a
              defect / delay / refund / damage / poor service, dissatisfaction,
              sarcasm, or a mixed "good BUT…" remark; OR (for reviews) a low
              rating. Still produce the personalised draft from the best
              NEGATIVE template, but set "action" to "handoff".

IMPORTANT for reviews — judge the TEXT, not just the star count. A high star
rating (4-5★) whose text criticizes or complains is a MISMATCH → "handoff".
A high rating with simple, criticism-free, positive text → "auto".

If the message is spam, abusive, or irrelevant, set action "handoff" and leave
"reply" empty.

Respond as STRICT JSON, no markdown:
{{"short_code": "<chosen template short_code>", "reasoning": "<one short sentence: why this template fits this message>", "reply": "<final reply text>", "action": "auto" | "handoff"}}
"""

# Human-friendly channel names for the chain-of-thought trace.
_CHANNEL_LABELS = {
    "review":   "Google review",
    "social":   "Instagram / Facebook DM",
    "whatsapp": "WhatsApp",
}


def _format_templates(templates: list[dict]) -> str:
    return "\n\n".join(
        f"[{t['short_code']}]\n{t['content']}" for t in templates
    )


# Star-rating → review template short_code. For rating-only reviews (no text)
# the AI has nothing to read, so we pick deterministically from the rating.
# The team still sees the suggestion card and decides to send.
_STAR_TEMPLATE_FALLBACK = {
    5: "review_positive_5star",
    4: "review_positive_can_improve",
    3: "review_acknowledge_feedback",
    2: "review_negative_will_work_on_it",
    1: "review_issue_not_resolved",
}


def _first_name(name: str) -> str:
    """First non-empty token of `name`, used for the 'Dear …' personalisation
    on rating-only review templates. Falls back to empty when the name is
    blank or looks like an auto-generated identifier."""
    parts = (name or "").strip().split()
    return parts[0] if parts and parts[0].lower() not in {"customer", "google", "user"} else ""


def _personalise(content: str, contact_name: str) -> str:
    """Swap the template's "Dear Customer," opening for "Dear <FirstName>,"
    when a real name is given. No-op otherwise so the template's wording
    stays exactly as the team approved it."""
    fn = _first_name(contact_name)
    if not fn:
        return content
    return content.replace("Dear Customer,", f"Dear {fn},", 1)


def _star_template_fallback(stars: int, contact_name: str,
                            templates: list[dict]) -> tuple[str, str, str]:
    """Pick a review template directly from the star rating (no LLM). Returns
    (reply, short_code, reasoning).

    Tries the star-matched template first; if that short_code isn't in
    Chatwoot (renamed/deleted), falls back to `review_acknowledge_feedback`
    (the most universally-applicable wording), and finally to any
    `review_*` template that exists — so the card is NEVER empty as long
    as at least one review template is seeded."""
    preferred = _STAR_TEMPLATE_FALLBACK.get(stars or 0,
                                            "review_acknowledge_feedback")
    by_code = {t.get("short_code"): t for t in templates}
    for code in (preferred, "review_acknowledge_feedback",
                 *(t.get("short_code") for t in templates)):
        match = by_code.get(code)
        if match and match.get("content"):
            reply = _personalise(match["content"], contact_name)
            reasoning = (f"Rating-only review ({stars or 'no'}★) — picked "
                         f"{code} (deterministic, no AI call).")
            return reply, code, reasoning
    return "", "", ""


def _unescape_newlines(text: str) -> str:
    """Defensive: the model sometimes double-escapes its `\\n` in the JSON
    output, so json.loads produces literal '\\n' substrings instead of real
    newline characters — and the card renders the raw text "Hello\\n\\nThank…"
    verbatim. Normalise the common whitespace escapes back to real characters.
    A legitimate reply never contains the visible string '\\n', so this is
    safe."""
    return (text
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t"))


def build_trace(channel: str, short_code: str, reasoning: str, action: str) -> list[dict]:
    """An AI chain-of-thought trace (same shape the DM bot emits) so agents see
    WHY this template was suggested. Rendered by the AiTrace.vue component when
    attached to a message's content_attributes.ai_trace."""
    chan = _CHANNEL_LABELS.get(channel, channel)
    steps = [
        {"type": "policy", "source": "system", "visibility": "internal",
         "label": "Channel", "detail": f"{chan} — Durian template suggestion"},
        {"type": "decision", "source": "rule", "visibility": "internal",
         "label": "Template chosen", "rule": short_code or "fallback",
         "detail": reasoning or "Best match for the customer's message."},
        {"type": "answer", "source": "model", "visibility": "public",
         "label": "Reply drafted",
         "detail": "Auto — safe to send" if action == "auto"
                   else "Flagged for human review before sending"},
    ]
    for i, s in enumerate(steps):
        s["i"] = i + 1
    return steps


# ── Review auto-reply gate ─────────────────────────────────────────────────
# A focused, temperature-0 classifier that decides whether a high-star review
# is GENUINELY positive — satisfaction with no complaint/criticism — so its
# reply can be auto-posted. Kept separate from the template picker because the
# auto-post is an outward, public action and needs a clean, consistent yes/no,
# not the noisier action field the template call produces.
_REVIEW_POSITIVE_SCHEMA = {
    "name":   "review_positivity",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "positive":   {"type": "boolean"},
            "confidence": {"type": "number"},
            "reason":     {"type": "string"},
        },
        "required": ["positive", "confidence", "reason"],
    },
}

_REVIEW_POSITIVE_PROMPT = (
    "You are validating whether a customer review can receive an AUTOMATED "
    "thank-you reply. Set positive=true ONLY when the review TEXT actually says "
    "something GOOD — it expresses praise, satisfaction, or thanks about the "
    "product, service, or experience. Brief or mild praise counts (e.g. 'good "
    "furniture, happy with my purchase' or 'nice showroom, satisfied').\n\n"
    "Set positive=false when the text:\n"
    "  • contains ANY complaint or criticism — a defect / delay / refund / "
    "damage / poor service, dissatisfaction, sarcasm, or a mixed 'good BUT…' "
    "remark — EVEN IF the star rating is high (a 4-5★ rating whose text "
    "complains is a mismatch); OR\n"
    "  • says nothing genuinely positive — it is neutral, purely factual, or "
    "empty (e.g. 'received the order', 'visited the store', 'ok'). A high star "
    "rating ALONE is not enough; the TEXT itself must contain real praise.\n\n"
    "When genuinely unsure, set positive=false. Output: positive (bool), a "
    "0.0-1.0 confidence, and a one-sentence reason."
)


async def classify_review_positive(message: str, stars: int = 0) -> dict:
    """Return {positive, confidence, reason} for a review. Conservative: any
    criticism, mixed sentiment, or uncertainty → positive=false. Fail-safe:
    returns positive=false on empty input / error so we never auto-post on a
    failed check."""
    import json
    default = {"positive": False, "confidence": 0.0, "reason": ""}
    if not (message or "").strip():
        return default
    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_schema", "json_schema": _REVIEW_POSITIVE_SCHEMA},
            messages=[
                {"role": "system", "content": _REVIEW_POSITIVE_PROMPT},
                {"role": "user",   "content": f"Star rating: {stars or '?'}/5\nReview: {message}"},
            ],
            name="review-positivity",
            metadata={"langfuse_tags": ["review", "positivity"]},
        )
        parsed = json.loads(r.choices[0].message.content or "")
    except Exception as e:
        print(f"[review-positivity] ERROR ({type(e).__name__}): {e}")
        return default
    return {"positive":   bool(parsed.get("positive")),
            "confidence": float(parsed.get("confidence") or 0),
            "reason":     (parsed.get("reason") or "")[:200]}


async def draft(channel: str, message: str, contact_name: str,
                stars: int = 0, location: str = ""):
    """Pick + personalise an approved template for the given channel.

    Returns a dict: {reply, action, short_code, reasoning, trace}. `trace` is an
    AI chain-of-thought (AiTrace.vue shape) explaining which template was chosen
    and why — attach it to the card message's content_attributes.ai_trace.

    Args:
        channel: short_code prefix — "review", "whatsapp", "instagram", "facebook".
        message: the customer's message (review text, WhatsApp/IG/FB body).
        contact_name: the customer/reviewer's name (for personalisation).
        stars: 1-5 review rating (review channel only — used for hard-handoff).
        location: showroom name (review channel only — for context).

    Reviews additionally hard-handoff below REVIEWS_AUTO_REPLY_MIN_STARS so
    low-rated reviews always need a human regardless of model output."""
    import json

    def result(reply, action, short_code="", reasoning=""):
        return {
            "reply": reply, "action": action,
            "short_code": short_code, "reasoning": reasoning,
            "trace": build_trace(channel, short_code, reasoning, action),
        }

    prefix = f"{channel}_"
    templates = [
        t for t in await chatwoot.list_canned_responses()
        if (t.get("short_code") or "").startswith(prefix)
    ]
    if not templates:
        print(f"[template_reply] no {prefix} templates found — handing off")
        return result("", "handoff")

    # Rating-only review (Google review with stars but no text): the AI has
    # nothing to read, so pick a template deterministically from the rating
    # and skip the LLM call entirely. Cheaper, faster, and avoids the
    # "(no draft)" empty-card UX. Reviews always go to the card (handoff)
    # regardless of star count.
    if channel == "review" and not (message or "").strip():
        reply, code, reasoning = _star_template_fallback(
            stars or 0, contact_name, templates)
        if reply:
            print(f"[template_reply] rating-only review ({stars}★) → "
                  f"{code} (no AI call)")
            return result(reply, "handoff", code, reasoning)
        # Fall through (no template matched) → handoff with no draft.
        return result("", "handoff")

    # Reviews auto-reply ONLY for genuinely positive high-star reviews:
    # the rating must be >= REVIEWS_AUTO_REPLY_MIN_STARS (default 4) AND the
    # model must judge the TEXT genuinely positive (action == "auto" below).
    # A 4-5★ rating whose text criticizes falls back to "handoff" via the
    # action check; anything below the star bar always goes to the card.
    force_human = (
        channel == "review"
        and (stars or 0) < config.REVIEWS_AUTO_REPLY_MIN_STARS
    )

    system_prompt = SYSTEM_PROMPT_FMT.format(
        channel_label=CHANNEL_LABELS.get(channel, channel),
        channel_warning=CHANNEL_WARNINGS.get(channel, ""),
    )

    context_lines = [f"From: {contact_name}"]
    if channel == "review":
        context_lines.append(f"Star rating: {stars or 'unknown'}/5")
        if location:
            context_lines.append(f"Showroom: {location}")
    context_lines.append(f"Message: {message or '(empty)'}")

    user_msg = (
        f"── APPROVED TEMPLATES ──\n{_format_templates(templates)}\n\n"
        f"── INCOMING MESSAGE ──\n" + "\n".join(context_lines)
    )

    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0.3,
            max_tokens=400,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            name="template-reply",
            metadata={
                "channel": channel,
                "stars": stars,
                "langfuse_tags": ["template_reply", f"channel_{channel}"],
            },
        )
        parsed = json.loads(r.choices[0].message.content)
        reply = _unescape_newlines((parsed.get("reply") or "").strip())
        action = (parsed.get("action") or "handoff").strip().lower()
        short_code = (parsed.get("short_code") or "").strip()
        reasoning = (parsed.get("reasoning") or "").strip()
    except Exception as e:
        print(f"[template_reply] ERROR ({type(e).__name__}): {e} — falling back")
        reply, action, short_code, reasoning = "", "handoff", "", ""

    # Universal safety net for reviews: if the AI returned no usable reply
    # (error, empty, hallucinated empty content), drop to the deterministic
    # star template so the card is NEVER blank.
    if channel == "review" and not reply:
        fb_reply, fb_code, fb_reason = _star_template_fallback(
            stars or 0, contact_name, templates)
        if fb_reply:
            print(f"[template_reply] AI returned no draft — falling back to "
                  f"{fb_code}")
            reply, short_code = fb_reply, fb_code
            reasoning = reasoning or fb_reason

    # Reviews at/above the star bar: the AUTO decision is made by the dedicated
    # positivity classifier (clean, temperature-0), NOT the noisy template
    # action — so a 4-5★ review only auto-posts when its TEXT is genuinely
    # positive. Below the bar, force_human already routes it to the card.
    if channel == "review" and not force_human and reply:
        pos = await classify_review_positive(message, stars)
        if pos["positive"] and pos["confidence"] >= config.REVIEW_AUTO_REPLY_MIN_CONFIDENCE:
            action = "auto"
        else:
            action = "handoff"
            print(f"[template_reply] {stars}★ review held for a human — "
                  f"text not clearly positive (conf {pos['confidence']:.2f}): "
                  f"{pos['reason']}")

    if force_human or action != "auto":
        return result(reply, "handoff", short_code, reasoning)

    return result(reply, "auto", short_code, reasoning)
