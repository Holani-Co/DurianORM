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

from pathlib import Path

import config
import chatwoot
# Instrumented client: review LLM calls are traced to Langfuse and nest under
# their conversation trace via explicit ids (lf_parent), like every other
# module. This runs fine in the poller's detached asyncio task — the earlier
# async-context error came from an `async with propagate_attributes` wrapper
# that no longer exists, NOT from this client (see tracing.py).
from llm_client import client

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None

# Per-template guidance from social_templates.yaml — the same file
# sync_social_templates.py pushes to Chatwoot, so wording and guidance stay in
# one place. Each entry's `use_when` + `triggers` are woven into the prompt so
# the model matches INTENT instead of guessing from the template body alone
# (which used to land catalogue asks on the generic greeting). Template CONTENT
# still comes live from Chatwoot (UI edits win); a code with no hint simply
# lists without guidance.
_HINTS_PATH = Path(__file__).parent / "social_templates.yaml"


def _load_hints() -> dict:
    if _yaml is None:
        return {}
    try:
        with open(_HINTS_PATH, "r", encoding="utf-8") as f:
            entries = (_yaml.safe_load(f) or {}).get("templates") or []
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[template_reply] failed to parse {_HINTS_PATH.name}: {e}")
        return {}
    return {t["short_code"]: t for t in entries if t.get("short_code")}


_HINTS = _load_hints()

# Display labels + warnings the system prompt weaves into the channel-specific
# instructions. Keep these short; the model adapts tone from the templates.
CHANNEL_LABELS = {
    "review":    "a Google review of one of our showrooms",
    "whatsapp":  "WhatsApp",
    "instagram": "an Instagram direct message (DM)",
    "facebook":  "a Facebook Messenger chat",
}

CHANNEL_WARNINGS = {
    "review":    "This reply is PUBLIC on Google — be extra careful. Never "
                 "quote prices, promise refunds/replacements, or admit fault.",
    "whatsapp":  "This reply is a private 1-to-1 WhatsApp message.",
    "instagram": "This reply is a PRIVATE Instagram DM. Write the complete, "
                 "helpful reply here — never tell the customer to 'check your "
                 "DM', they are already in it.",
    "facebook":  "This reply is a PRIVATE Facebook Messenger chat. Write the "
                 "complete, helpful reply here — never tell the customer to "
                 "'check your inbox', they are already in it.",
}


SYSTEM_PROMPT_FMT = """\
You are the brand voice of Durian, an Indian premium furniture retailer.
You are writing a reply to a customer on {channel_label}.

{channel_warning}

You are given a set of APPROVED reply templates (each with a short_code, and
usually USE WHEN guidance plus TYPICAL MESSAGES examples) and the customer's
recent message(s) — the LAST message is the one you are replying to; earlier
ones are context. Your job:

1. PICK the single template that best fits the LATEST message's intent.
   Match against each template's USE WHEN / TYPICAL MESSAGES first, sentiment
   second. When the customer asks for something specific (a catalogue, a
   price, a store address, a callback, a job), NEVER pick a generic greeting
   template if a specific one exists.
2. PERSONALISE it lightly:
   - Replace the [NAME] placeholder (or "Dear Customer") with the sender's
     first name if one is given (e.g. "Hello Rajiv,"). If no real name is
     available, drop the placeholder ("Hello," / "Dear Customer,").
   - Fill obvious placeholders you have the answer for (e.g. the product name
     when the customer named one). If a template needs a substitution you
     CANNOT make (like a per-product URL you weren't given), prefer a
     template without that placeholder.
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

"needs_human" is a SEPARATE, stricter flag — true ONLY when auto-replying would
be risky and a PERSON must handle it. Set needs_human=true ONLY for an EXPLICIT:
  • legal threat / intent to sue — lawyer, court, consumer forum/court, police,
    legal notice, "I'll take legal action";
  • abusive, obscene, or threatening language, or a defamatory personal
    accusation;
  • specific fraud / scam accusation framed as intent to expose or escalate
    (not the word "cheated"/"fraud" used loosely as an insult);
  • safety / health hazard that caused or risks injury (fire, shock, injury).
EVERYTHING ELSE is needs_human=FALSE. Ordinary negativity — even strongly worded
("worst company", "pathetic quality", "zero service", "very disappointed",
"waste of money") — plus delivery delays, defects, and rude-staff complaints all
get their apology template AUTOMATICALLY. Do NOT flag a review needs_human just
because it is angry or 1★; only an explicit escalation above qualifies.

Respond as STRICT JSON, no markdown:
{{"short_code": "<chosen template short_code>", "reasoning": "<one short sentence: why this template fits this message>", "reply": "<final reply text>", "action": "auto" | "handoff", "needs_human": true | false}}
"""

# Human-friendly channel names for the chain-of-thought trace.
_CHANNEL_LABELS = {
    "review":    "Google review",
    "instagram": "Instagram",
    "facebook":  "Facebook",
    "whatsapp":  "WhatsApp",
}


def _format_templates(templates: list[dict]) -> str:
    blocks = []
    for t in templates:
        code = t["short_code"]
        hint = _HINTS.get(code) or {}
        head = f"[{code}]"
        if hint.get("use_when"):
            head += f"\nUSE WHEN: {str(hint['use_when']).strip()}"
        triggers = hint.get("triggers") or []
        if triggers:
            head += "\nTYPICAL MESSAGES: " + "; ".join(str(x) for x in triggers[:6])
        blocks.append(f"{head}\n{t['content']}")
    return "\n\n".join(blocks)


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


async def draft(channel: str, message: str, contact_name: str,
                stars: int = 0, location: str = "", lf_parent: dict = None,
                surface: str = ""):
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
        surface: "comment" when drafting a PUBLIC reply to a post comment —
            narrows the template pool to the comment variants (short, prices
            redirected to DM) and swaps in a public-reply warning.

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
    all_templates = [
        t for t in await chatwoot.list_canned_responses()
        if (t.get("short_code") or "").startswith(prefix)
    ]
    if not all_templates:
        print(f"[template_reply] no {prefix} templates found — handing off")
        return result("", "handoff")

    # Comment vs DM pools are kept STRICTLY separate so the two never get
    # confused. Comment-surface templates (marked in the YAML, code prefix
    # social_comment_) are short PUBLIC replies that redirect questions to DM;
    # DM drafts must exclude them (a DM must never say "check your DM").
    comment_codes = {c for c, h in _HINTS.items()
                     if (h.get("surface") or "") == "comment"}
    if surface == "comment":
        templates = [t for t in all_templates
                     if t.get("short_code") in comment_codes]
        if not templates:
            # No comment templates synced — NEVER post a DM body publicly.
            # Fall back to the redirect-to-DM catch-all, else hand off.
            templates = [t for t in all_templates
                         if t.get("short_code") == f"{channel}_comment_redirect_to_dm"]
            if not templates:
                return result("", "handoff")
    else:
        # DM (and review) pool: everything EXCEPT comment templates.
        templates = [t for t in all_templates
                     if t.get("short_code") not in comment_codes] or all_templates

    # Rating-only review (Google review with stars but no text): the AI has
    # nothing to read, so pick a template deterministically from the rating
    # and skip the LLM call entirely. Cheaper, faster, and avoids the
    # "(no draft)" empty-card UX.
    #
    # AUTO vs HANDOFF for rating-only: with no text there is no sentiment to
    # misread, so a high rating (>= REVIEWS_AUTO_REPLY_MIN_STARS, default 4★)
    # is unambiguously positive — auto-reply directly, no LLM positivity check
    # needed. A bare 1-3★ still goes to the agent card. (The master switch
    # REVIEWS_AUTO_REPLY is enforced by the caller/poller before posting.)
    if channel == "review" and not (message or "").strip():
        reply, code, reasoning = _star_template_fallback(
            stars or 0, contact_name, templates)
        if reply:
            rating_only_auto = (stars or 0) >= config.REVIEWS_AUTO_REPLY_MIN_STARS
            action = "auto" if rating_only_auto else "handoff"
            print(f"[template_reply] rating-only review ({stars}★) → "
                  f"{code} ({action}, no AI call)")
            return result(reply, action, code, reasoning)
        # Fall through (no template matched) → handoff with no draft.
        return result("", "handoff")

    # Zero-touch policy: all ratings >= REVIEWS_AUTO_REPLY_MIN_STARS (now 1) are
    # eligible for auto-reply. The "really bad content" severity check below is
    # what actually holds a review for a human — force_human is just the star
    # floor, kept configurable so a rating band could still be excluded.
    force_human = (
        channel == "review"
        and (stars or 0) < config.REVIEWS_AUTO_REPLY_MIN_STARS
    )

    channel_label = CHANNEL_LABELS.get(channel, channel)
    channel_warning = CHANNEL_WARNINGS.get(channel, "")
    if surface == "comment":
        channel_label = "a PUBLIC comment under one of our Instagram/Facebook posts"
        channel_warning = ("This reply is PUBLIC under our post. Keep it short "
                           "and brand-safe. NEVER quote prices publicly — "
                           "questions get redirected to DM (the comment "
                           "templates already do this). Pick ONLY from the "
                           "templates shown below; they are all comment-safe.")
    system_prompt = SYSTEM_PROMPT_FMT.format(
        channel_label=channel_label,
        channel_warning=channel_warning,
    )

    context_lines = [f"From: {contact_name}"]
    if channel == "review":
        context_lines.append(f"Star rating: {stars or 'unknown'}/5")
        if location:
            context_lines.append(f"Showroom: {location}")
    context_lines.append(f"Message(s):\n{message or '(empty)'}")

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
            metadata={"channel": channel, "stars": stars, "surface": surface,
                      "langfuse_tags": ["template_reply", f"channel_{channel}"]},
            **(lf_parent or {}),
        )
        parsed = json.loads(r.choices[0].message.content)
        reply = _unescape_newlines((parsed.get("reply") or "").strip())
        action = (parsed.get("action") or "handoff").strip().lower()
        short_code = (parsed.get("short_code") or "").strip()
        reasoning = (parsed.get("reasoning") or "").strip()
        # needs_human: really-bad content (abuse / defamation / legal-suing /
        # fraud / safety) that a person must handle. Rides on this same call —
        # no separate classifier — and is used only by the review flow below.
        needs_human = bool(parsed.get("needs_human"))
    except Exception as e:
        print(f"[template_reply] ERROR ({type(e).__name__}): {e} — falling back")
        reply, action, short_code, reasoning = "", "handoff", "", ""
        needs_human = True  # fail safe → a human looks at it

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

    # Reviews (zero-touch): auto-post the rating-appropriate template for EVERY
    # rating UNLESS this same template call flagged the content as needs_human —
    # really bad content (abuse / defamation / legal-suing / fraud / safety) that
    # must go to a person. No extra AI call; needs_human rides on the template
    # response. Ordinary negativity (delay, defect, poor service) auto-replies.
    if channel == "review" and not force_human and reply:
        action = "handoff" if needs_human else "auto"
        if needs_human:
            print(f"[template_reply] {stars}★ review held for a human — "
                  f"content flagged needs_human")

    if force_human or action != "auto":
        return result(reply, "handoff", short_code, reasoning)

    return result(reply, "auto", short_code, reasoning)
