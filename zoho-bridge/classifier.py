# OpenAI-backed team classifier. One LLM call → one team name.
# Falls back to 'support' on any error so a model hiccup never blocks routing.

import httpx

import config

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
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       config.OPENAI_MODEL,
                    "temperature": 0,
                    "max_tokens":  5,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                },
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip().lower()
            # Strip trailing punctuation just in case
            raw = raw.rstrip(".!?,;:")
            return raw if raw in VALID_TEAMS else "support"
    except Exception as e:
        print(f"[classifier] ERROR ({type(e).__name__}): {e} — falling back to 'support'")
        return "support"
