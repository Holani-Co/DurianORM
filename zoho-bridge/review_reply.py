# OpenAI-backed review reply drafter, Durian voice, rating-aware.
#
# Returns (reply_text, action) where action ∈ {"auto", "handoff"}.
#   - "auto"    → safe to post automatically (positive / simple).
#   - "handoff" → needs a human (complaint, low rating, anything risky).
#
# The decision combines a hard star gate with the model's own judgement,
# mirroring the Instagram-comment bot's HANDOFF convention.

import httpx

import config

SYSTEM_PROMPT = """\
You are the brand voice of Durian, an Indian premium furniture retailer,
writing a PUBLIC reply to a Google review of one of our showrooms.

── HARD RULES ───────────────────────────────────────────────────────────
- Keep it warm, professional, specific, and brief (1–3 sentences).
- Sign off naturally as Team Durian. Never use hashtags or emojis overload
  (at most one tasteful emoji, usually none).
- Use the reviewer's name if given. Reference what they mentioned when natural.
- NEVER quote prices, promise refunds/replacements, or admit legal fault.
- If the review is positive (praise, high rating, thanks), reply with a warm
  on-brand thank-you.
- If the review is negative, a complaint, mentions a defect/delay/refund/
  damage/poor service, OR is low-rated, do NOT attempt to resolve it publicly.
  Respond with EXACTLY the single word: HANDOFF
- If the review is spam, abusive, or irrelevant, respond with: HANDOFF
- When unsure, prefer HANDOFF.

Output ONLY the reply text, or the single word HANDOFF. No quotes, no labels.
"""


async def draft(stars: int, comment: str, reviewer: str, location_title: str):
    """Return (reply_text, action). Hard-handoff anything at or below the
    configured star threshold regardless of model output."""
    # Hard gate: low stars always go to a human.
    force_human = stars and stars < config.REVIEWS_AUTO_REPLY_MIN_STARS

    user_msg = (
        f"Showroom: {location_title}\n"
        f"Reviewer: {reviewer}\n"
        f"Star rating: {stars or 'unknown'}/5\n"
        f"Review text: {comment or '(no text, rating only)'}"
    )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       config.OPENAI_MODEL,
                    "temperature": 0.4,
                    "max_tokens":  160,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                },
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[review_reply] ERROR ({type(e).__name__}): {e} — handing off")
        return ("", "handoff")

    model_handoff = text.upper().strip().strip(".!") == "HANDOFF"

    if force_human or model_handoff:
        # Provide a suggested draft for the human even on handoff (unless the
        # model itself refused). A thank-you template helps the agent start.
        suggestion = "" if model_handoff else text
        return (suggestion, "handoff")

    return (text, "auto")
