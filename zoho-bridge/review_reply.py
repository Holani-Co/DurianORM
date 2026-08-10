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

import re
import time
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

# Review templates deliberately live OUTSIDE social_templates.yaml — that file is
# the SOCIAL canned-response source (its sync requires a `content` body), while the
# review wording is owned by setup_review_templates.py. Their SELECTION guidance
# still belongs with the other hints, so it's defined here and merged in below.
#
# Without this the model saw no USE WHEN for any review template and fell back to
# "sentiment", so every strongly-worded 1★ landed on the most apologetic body —
# review_issue_not_resolved. That template is only right for someone who has
# ALREADY chased us and is still waiting; a first-time complaint needs
# review_negative_info_needed so we can collect details and actually act on it.
_REVIEW_HINTS = {
    "review_negative_info_needed": {
        "use_when": (
            "THE DEFAULT for a negative review. The customer describes a bad "
            "experience — damage, defect, delay, poor quality, bad service, rude "
            "staff, no accountability — but gives us nothing to act on: no "
            "complaint/ticket number and no sign they have contacted us about it "
            "before. However long or angry the review is, if this is the first we "
            "are hearing of it, we need their contact details to route it."
        ),
        "triggers": [
            "I had the worst experience with Durian. Chairs arrived damaged and the sofa is still not ready",
            "Worst quality and even worse services, I would advise never to buy from them",
            "Delivery delayed well past the promised date and nobody is taking accountability",
            "Poor product quality and very disappointing experience",
        ],
    },
    "review_issue_not_resolved": {
        "use_when": (
            "ONLY when the review itself shows the customer ALREADY engaged us and "
            "is STILL waiting — they cite a complaint/ticket/docket number, say "
            "they have complained or followed up multiple times, or say we "
            "promised a fix that never came. That prior-contact history must be "
            "present in the text. NEVER use this for a first-time complaint, "
            "however severe — that is review_negative_info_needed."
        ),
        "triggers": [
            "I have complained more than five times and nobody has resolved it",
            "Complaint number 102215 was raised a month ago and there is still no resolution",
            "Your team promised a replacement weeks ago and I am still waiting",
            "I keep following up and the issue is still not fixed",
        ],
    },
    "review_negative_will_work_on_it": {
        "use_when": (
            "Mild or vague dissatisfaction with NO specific problem we could act "
            "on and no prior contact — the experience simply did not meet "
            "expectations. If the customer names a concrete problem (damage, "
            "delay, defect, service failure), use review_negative_info_needed "
            "instead so we can collect their details."
        ),
        "triggers": [
            "Not up to the mark",
            "Expected better from this brand",
            "Average experience, nothing special",
        ],
    },
    "review_issue_resolved": {
        "use_when": (
            "The customer indicates their problem HAS since been sorted out, or is "
            "updating a earlier complaint to say it was handled. Never use this "
            "while a complaint is still open."
        ),
        "triggers": [
            "Update: the team replaced it and everything is fine now",
            "Issue has been resolved by your service team, thank you",
        ],
    },
    "review_resolved_negative": {
        "use_when": (
            "We have already offered every possible resolution and the customer "
            "remains unhappy — a post-resolution standoff, visible in the text as "
            "the customer rejecting or dismissing what was offered."
        ),
        "triggers": [
            "They offered a repair but I wanted a refund, still not acceptable",
            "You have done nothing useful despite all your so-called resolutions",
        ],
    },
    "review_acknowledge_feedback": {
        "use_when": (
            "Neutral, mixed, or suggestion-style feedback with no real complaint to "
            "resolve — the customer is offering an opinion or an idea rather than "
            "reporting a problem."
        ),
        "triggers": [
            "Good range but the showroom could use more seating options",
            "Please consider opening a store in our city",
        ],
    },
    "review_positive_5star": {
        "use_when": (
            "Clear praise with no criticism at all — the customer is happy with the "
            "product, the staff, or the experience."
        ),
        "triggers": [
            "Excellent quality and great service, very happy with my purchase",
            "Loved the showroom and the staff were very helpful",
        ],
    },
    "review_positive_can_improve": {
        "use_when": (
            "Positive overall, but the customer adds a small suggestion or a minor "
            "niggle alongside the praise."
        ),
        "triggers": [
            "Great sofa and good service, delivery could have been a bit quicker",
            "Happy with the purchase, wish there were more colour options",
        ],
    },
}
# YAML wins if a review code is ever added to social_templates.yaml.
_HINTS = {**_REVIEW_HINTS, **_HINTS}

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

1. PICK the single approved template that fits what the customer needs RIGHT NOW,
   read IN THE CONTEXT OF THE WHOLE CONVERSATION above — not the latest line in
   isolation. Match each template's USE WHEN / TYPICAL MESSAGES first, sentiment
   second.
   - Resolve follow-ups from the conversation: if we already asked for details and
     the latest message PROVIDES them (name / phone / address / pincode / email),
     pick the "shared their details / thank you" acknowledgment template — NEVER
     re-send a request for details you already have.
   - When the customer asks for something specific (catalogue, price, store
     address, callback, job), never pick a generic greeting template if a
     specific one exists.
   HAND OFF INSTEAD OF GUESSING — the most important rule: if NO approved template
   genuinely fits what the customer is asking (they need an answer / price /
   decision no template covers, the request is outside these templates, or you'd
   have to invent or guess), set "action": "handoff" and leave "reply" EMPTY. A
   human is ALWAYS better than a loosely-related or wrong template. Only reply
   when a template clearly and fully fits the conversation.
   Do NOT stretch a template because a KEYWORD matches — the template's USE WHEN
   must match the customer's actual INTENT. (E.g. the word "supplier" does not
   make a "become our vendor/supplier" template fit a customer asking to buy from
   our suppliers.) When the intent doesn't clearly match any template's USE WHEN,
   hand off.
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

"confidence" (0-100) is how sure you are that the chosen template is the correct,
complete, and safe reply to send to this customer AS-IS. Be conservative: use a
value BELOW 80 when the message is ambiguous, asks something the templates don't
cover, mixes several requests, needs specifics you don't have, or could be
sensitive — those should reach a human. Use 80+ only for a clear, ordinary
message that one template answers well.

"order_status_enquiry" = true ONLY when the customer is asking about an order they
have ALREADY placed — its status / delivery / tracking ("where is my order", "I
bought a sofa last week, what's the status?", "my order hasn't arrived"). FALSE
for a NEW purchase enquiry ("I want to buy", "is X available", "price of X"), and
FALSE for general conversation or a complaint with no order-status question.

"is_complaint" = true when the customer is dissatisfied / raising a grievance —
"I'm not happy", "poor quality", "damaged", "delayed", "worst service", "I have a
complaint". This is SEPARATE from needs_human: an ordinary complaint is
is_complaint=true, needs_human=false (we reply with the apology template
automatically); only an EXPLICIT escalation is also needs_human=true.

Respond as STRICT JSON, no markdown:
{{"short_code": "<chosen template short_code>", "reasoning": "<one short sentence: why this template fits this message>", "reply": "<final reply text>", "action": "auto" | "handoff", "needs_human": true | false, "confidence": <integer 0-100>, "order_status_enquiry": true | false, "is_complaint": true | false}}
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
    # A rating-only 1★ tells us nothing beyond "unhappy" — no complaint history,
    # nothing to act on — so it takes the same route as any first-time complaint:
    # ask for contact details. review_issue_not_resolved is reserved for customers
    # who have already chased us (see _REVIEW_HINTS).
    1: "review_negative_info_needed",
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


def _renumber(steps: list[dict]) -> list[dict]:
    for i, s in enumerate(steps):
        s["i"] = i + 1
    return steps


def build_trace(channel: str, short_code: str, reasoning: str, action: str,
                *, confidence: int = None, is_complaint: bool = False,
                needs_human: bool = False) -> list[dict]:
    """An AI chain-of-thought trace (same shape the DM bot emits) so agents see
    WHY this reply was produced. Rendered by AiTrace.vue when attached to a
    message's content_attributes.ai_trace.

    Covers how the message was READ (sentiment) and how the reply was CHOSEN.
    The final "what we did with it" step is appended by the caller via
    add_outcome_step(), because the auto-send gate lives there."""
    chan = _CHANNEL_LABELS.get(channel, channel)

    # How the model read the customer — the "sentiment" an agent asks about.
    if needs_human:
        sentiment = "Serious — legal / abuse / fraud / safety escalation"
    elif is_complaint:
        sentiment = "Negative — a complaint"
    elif action == "auto":
        sentiment = "Positive / neutral — no complaint detected"
    else:
        sentiment = "Negative or unclear — contains criticism"

    steps = [
        {"type": "policy", "source": "system", "visibility": "internal",
         "label": "Channel", "detail": f"{chan} — Durian template suggestion"},
        {"type": "observation", "source": "model", "visibility": "internal",
         "label": "Message read as", "detail": sentiment},
        {"type": "decision", "source": "rule", "visibility": "internal",
         "label": "Template chosen", "rule": short_code or "fallback",
         "detail": reasoning or "Best match for the customer's message."},
        {"type": "answer", "source": "model", "visibility": "public",
         "label": "Reply drafted",
         "detail": ("Safe to send automatically" if action == "auto"
                    else "Flagged for human review before sending")
                   + (f" · confidence {confidence}%" if confidence is not None else "")},
    ]
    return _renumber(steps)


def add_outcome_step(trace: list[dict], *, sent: bool, detail: str) -> list[dict]:
    """Append the final 'what actually happened' step. This is what tells an
    agent whether the reply went out on its own — and when it didn't, exactly
    WHY it was held back (below the confidence bar, handed off, auto-send
    switched off). Callers own the gate, so they own this step."""
    trace = list(trace or [])
    trace.append({
        "type": "outcome",
        "source": "system",
        "visibility": "internal",
        "label": "Sent automatically" if sent else "Held for an agent",
        "detail": detail,
    })
    return _renumber(trace)


async def draft(channel: str, message: str, contact_name: str,
                stars: int = 0, location: str = "", lf_parent: dict = None,
                surface: str = "", conversation: str = "", known_facts: str = ""):
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

    def result(reply, action, short_code="", reasoning="", confidence=0,
               order_status_enquiry=False, is_complaint=False,
               needs_human=False):
        return {
            "reply": reply, "action": action,
            "short_code": short_code, "reasoning": reasoning,
            "confidence": confidence,
            "order_status_enquiry": order_status_enquiry,
            "is_complaint": is_complaint,
            "needs_human": needs_human,
            "trace": build_trace(channel, short_code, reasoning, action,
                                 confidence=confidence, is_complaint=is_complaint,
                                 needs_human=needs_human),
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
    # Durable facts already known about this customer (distilled from the whole
    # thread, which may be longer than the windowed transcript below). This is
    # what stops the drafter re-asking for the same details in circles.
    if known_facts:
        context_lines.append(
            "── WHAT WE ALREADY KNOW ABOUT THIS CUSTOMER ──\n" + known_facts + "\n"
            "IMPORTANT: We already have every detail listed above — do NOT ask "
            "for any of them again. If we already have their name, contact "
            "number and city/location, do NOT pick a details-collection "
            "template; acknowledge and tell them our showroom/team will reach "
            "out shortly, or answer their actual question. Always move the "
            "conversation forward — never repeat a question they've answered.")
    # The FULL conversation (both sides), so the template is chosen for the whole
    # exchange in context — not a single message in isolation. This is what stops
    # re-asking for details already given and lets follow-ups be understood.
    if conversation:
        context_lines.append(f"── CONVERSATION SO FAR (oldest first) ──\n{conversation}")
    context_lines.append(f"── THE CUSTOMER'S LATEST MESSAGE(S) TO REPLY TO ──\n{message or '(empty)'}")

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
        # confidence (0-100): how sure the model is the template fits as-is.
        # Drives social auto-send (handle_template_suggest); a bad/missing value
        # falls to 0 → not confident → review card, not auto-send.
        try:
            confidence = max(0, min(100, int(parsed.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        # order_status_enquiry: the customer is asking about an EXISTING order —
        # routes social DMs into the BMS order-lookup flow (handle_template_suggest).
        order_status_enquiry = bool(parsed.get("order_status_enquiry"))
        # is_complaint: ordinary dissatisfaction → social auto-replies the apology
        # template (unless needs_human flags a serious escalation → still a human).
        is_complaint = bool(parsed.get("is_complaint"))
    except Exception as e:
        print(f"[template_reply] ERROR ({type(e).__name__}): {e} — falling back")
        reply, action, short_code, reasoning = "", "handoff", "", ""
        needs_human = True  # fail safe → a human looks at it
        confidence = 0
        order_status_enquiry = False
        is_complaint = False

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

    # Social (Instagram / Facebook / WhatsApp): serious escalations always go to a
    # human; an ordinary complaint gets its apology template auto-replied (client
    # rule — complaints should be answered, not carded). Reviews are unchanged
    # (their auto/handoff was already decided by needs_human above).
    if channel != "review" and reply:
        if needs_human:
            action = "handoff"
        elif is_complaint:
            action = "auto"

    if force_human or action != "auto":
        return result(reply, "handoff", short_code, reasoning, confidence,
                      order_status_enquiry, is_complaint, needs_human)

    return result(reply, "auto", short_code, reasoning, confidence,
                  order_status_enquiry, is_complaint, needs_human)


# ── Reply bank (Feature: vertical × case × 10 rotating variants) ────────────
# The client's review reply bank (review_reply_bank.yaml): for each vertical
# (furniture / fhc / doors) a set of POSITIVE/NEGATIVE cases, each with ~10
# phrasing variants. draft_review() classifies a review into (vertical, case)
# via ONE LLM call (also extracting the reviewer/staff/product to fill the
# [brackets]), then rotates through the case's variants (reviews_state) so
# consecutive same-case reviews read differently.
#
# SOURCE OF TRUTH: the variant TEXT is owned by Chatwoot canned responses
# (short_code review_<vertical>_<case>_NN, seeded by sync_review_bank.py) so the
# client edits/adds/removes wording from the UI and it changes what the AI drafts
# — UI edits win. The YAML is the structural SEED (which verticals/cases exist,
# each case's sentiment) AND the fallback text: if a case has no canned responses
# synced (or the fetch fails), draft_review falls back to the YAML options, so a
# missing/un-synced template can never break the drafter. Adding a whole new CASE
# still needs a YAML change (the classifier reads the case list + sentiment from
# YAML); editing or adding VARIANTS within an existing case is fully UI-driven.
_REVIEW_BANK_PATH = Path(__file__).parent / "review_reply_bank.yaml"
_review_bank_cache = None

# UI-override cache: {vertical: {case: [option, ...]}} parsed from the
# review_<vertical>_<case>_NN canned responses, refreshed every _LIVE_BANK_TTL s
# (reviews are low-volume; this keeps us from fetching per review). The bank
# lives under the `review_` prefix so the Canned Responses UI files it under the
# "Google Reviews" tab (templateTaxonomy.js keys tabs on the pre-underscore
# prefix). Safe now that the edit/regenerate path uses draft_review, not the old
# draft(channel="review") picker — so no legacy consumer of bare review_ codes
# remains to collide with.
_LIVE_BANK_TTL = 300.0
_live_bank_cache = {"at": -1e9, "data": {}}
_RE_REVIEW_CODE = re.compile(r"^review_(furniture|fhc|doors)_(.+)_(\d+)$")


def _load_review_bank() -> dict:
    global _review_bank_cache
    if _review_bank_cache is None:
        try:
            with open(_REVIEW_BANK_PATH, encoding="utf-8") as f:
                _review_bank_cache = (_yaml.safe_load(f) or {}).get("verticals") or {}
        except Exception as e:
            print(f"[review-bank] could not load {_REVIEW_BANK_PATH.name}: {e}")
            _review_bank_cache = {}
    return _review_bank_cache


async def _review_bank_live() -> dict:
    """{vertical: {case: [option, ...]}} built from the review_<vertical>_<case>_NN
    canned responses (the UI-editable source), variants ordered by NN. Cached for
    _LIVE_BANK_TTL. Returns {} on any failure so the caller falls back to the YAML
    seed — a Chatwoot hiccup must never take the drafter down."""
    now = time.monotonic()
    if now - _live_bank_cache["at"] < _LIVE_BANK_TTL:
        return _live_bank_cache["data"]
    buckets: dict = {}
    try:
        for cr in await chatwoot.list_canned_responses():
            m = _RE_REVIEW_CODE.match((cr.get("short_code") or "").strip())
            if not m:
                continue
            vert, case, nn = m.group(1), m.group(2), int(m.group(3))
            buckets.setdefault(vert, {}).setdefault(case, []).append((nn, cr.get("content") or ""))
    except Exception as e:
        print(f"[review-bank] live canned-response fetch failed: {e}")
        _live_bank_cache.update(at=now, data={})
        return {}
    data = {vert: {case: [c for _, c in sorted(opts)] for case, opts in cases.items()}
            for vert, cases in buckets.items()}
    _live_bank_cache.update(at=now, data=data)
    return data


def review_vertical_for(location: str) -> str:
    """Which reply-bank vertical this showroom belongs to, from its name.
    'Durian Doors - …' → doors; an FHC / Home Studio store → fhc; else furniture
    (the default and largest network)."""
    loc = (location or "").lower()
    if "door" in loc:
        return "doors"
    if "fhc" in loc or "full home" in loc or "home studio" in loc or "home customi" in loc:
        return "fhc"
    return "furniture"


def _store_display(location: str) -> str:
    """A short store name for the [store] bracket — the locality after the last
    ' - ' (e.g. 'Durian Furniture - Pune - Creaticity' → 'Pune - Creaticity')."""
    parts = [p.strip() for p in (location or "").split(" - ") if p.strip()]
    if len(parts) >= 3:
        return " - ".join(parts[-2:])
    return parts[-1] if parts else (location or "our showroom")


def _fill_review_brackets(text: str, *, name: str, staff: str, store: str,
                          product: str) -> str:
    """Fill the reply-bank [brackets] with the extracted values, falling back to
    natural generics so a reply NEVER goes out with a literal [bracket] or an
    awkward empty slot."""
    repl = {
        "[Name]": name or "there",
        "[staff member]": staff or "our team",
        "[Staff member]": staff or "Our team",
        "[store]": store or "our showroom",
        "[product]": product or "your purchase",
        "[modular kitchen/wardrobe]": product or "your project",
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    # Safety net: any bracket we didn't map → a neutral phrase, never a literal [x].
    import re as _re
    text = _re.sub(r"\[[^\]]+\]", "your purchase", text)
    return text


def _review_case_schema(case_keys: list) -> dict:
    return {
        "name": "review_case_classification", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "case":         {"type": "string", "enum": case_keys or ["positive_generic"]},
                "needs_human":  {"type": "boolean"},
                "staff_member": {"type": "string"},
                "product":      {"type": "string"},
                "reasoning":    {"type": "string"},
            },
            "required": ["case", "needs_human", "staff_member", "product", "reasoning"],
        },
    }


def _review_case_prompt(vertical: str, cases: dict, stars: int) -> str:
    lines = [
        f"You are triaging a Google review of a Durian {vertical.upper()} showroom "
        f"so it gets the right reply from an approved reply bank.",
        f"Star rating: {stars or 'unknown'}/5 (a strong signal: 1-2 = negative "
        "case, 4-5 = positive; judge the TEXT too — a low star with happy text is "
        "a mis-tap, a high star with a complaint is negative).",
        "",
        "Pick the single best-fitting CASE:",
    ]
    for key, cfg in cases.items():
        lines.append(f"  - {key}  ({(cfg or {}).get('sentiment','')})")
    lines += [
        "",
        "Also extract, for personalising the reply (empty string if not present):",
        "  - staff_member: the exact name of any staff member the reviewer praises "
        "or blames (e.g. 'Guddu Kumar', 'Richa'). Empty if none named.",
        "  - product: the specific product/item mentioned (e.g. 'sofa', 'recliner', "
        "'modular kitchen', 'wardrobe', 'door'). Empty if none.",
        "  - needs_human: true ONLY for genuinely serious content — legal threats, "
        "fraud/scam allegations, safety hazards, or abuse. An ordinary complaint is "
        "NOT needs_human (it gets an empathetic reply from the bank).",
        "  - reasoning: one short sentence on why this case.",
        "Output STRICT JSON per the schema.",
    ]
    return "\n".join(lines)


async def draft_review(message: str, contact_name: str, stars: int = 0,
                       location: str = "", lf_parent: dict = None) -> dict:
    """Draft a Google-review reply from the reply bank. Returns the same shape as
    draft(): {reply, action, short_code, reasoning, confidence, needs_human,
    trace}. short_code is 'review:<vertical>:<case>' for traceability.

    action == 'auto' → safe to post publicly; 'handoff' → serious content, card
    for a human. Rotation (reviews_state) makes repeat cases read differently."""
    import json
    import reviews_state as _state

    vertical = review_vertical_for(location)
    bank = _load_review_bank()
    cases = ((bank.get(vertical) or {}).get("cases")) or {}
    # UI-editable variant text (canned responses) overlaid per (vertical, case);
    # falls back to the YAML seed below when a case has none synced.
    live_vert = (await _review_bank_live()).get(vertical) or {}
    reviewer = (contact_name or "").strip()
    store = _store_display(location)

    if not cases:
        # No bank for this vertical → hand off with no draft (never guess).
        return result_review("", "handoff", f"review:{vertical}:none",
                             "No reply bank for this vertical.", stars, needs_human=False)

    case_keys = list(cases.keys())
    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL, temperature=0, max_tokens=200,
            response_format={"type": "json_schema",
                             "json_schema": _review_case_schema(case_keys)},
            messages=[
                {"role": "system", "content": _review_case_prompt(vertical, cases, stars)},
                {"role": "user", "content": f"REVIEW:\n{(message or '(no text — rating only)')[:1500]}"},
            ],
            name="review-case-classification",
            metadata={"langfuse_tags": ["reviews", "reply-bank"]},
            **(lf_parent or {}),
        )
        parsed = json.loads(r.choices[0].message.content or "{}")
    except Exception as e:
        print(f"[review-bank] classify failed: {e}")
        return result_review("", "handoff", f"review:{vertical}:error",
                             "Classifier error — needs a human.", stars, needs_human=False)

    case = parsed.get("case") if parsed.get("case") in cases else case_keys[0]
    needs_human = bool(parsed.get("needs_human"))
    staff = (parsed.get("staff_member") or "").strip()
    product = (parsed.get("product") or "").strip()
    reasoning = (parsed.get("reasoning") or "")[:200]

    # UI edits win: live canned-response variants for this case, else YAML seed.
    options = live_vert.get(case) or (cases.get(case) or {}).get("options") or []
    if not options:
        return result_review("", "handoff", f"review:{vertical}:{case}",
                             "No options in this case.", stars, needs_human=needs_human)
    idx = _state.next_reply_index(f"{vertical}:{case}", len(options))
    reply = _fill_review_brackets(options[idx], name=reviewer, staff=staff,
                                  store=store, product=product)

    # Serious content → hand off for a human even though we drafted a reply.
    action = "handoff" if needs_human else "auto"
    reasoning = (f"{vertical} · {case} · variant {idx + 1}/{len(options)}"
                 + (f" — {reasoning}" if reasoning else ""))
    return result_review(reply, action, f"review:{vertical}:{case}", reasoning,
                         stars, needs_human=needs_human)


def result_review(reply, action, short_code, reasoning, stars, needs_human=False):
    """Shape a draft_review return identically to draft() so the poller consumes
    it unchanged."""
    return {
        "reply": reply, "action": action, "short_code": short_code,
        "reasoning": reasoning, "confidence": 100 if action == "auto" else 0,
        "order_status_enquiry": False, "is_complaint": False,
        "needs_human": needs_human,
        "trace": build_trace("review", short_code, reasoning, action,
                             confidence=(100 if action == "auto" else 0),
                             needs_human=needs_human),
    }
