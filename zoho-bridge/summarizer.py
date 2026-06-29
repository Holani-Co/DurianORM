# OpenAI-backed conversation summariser for Zoho Desk tickets.
#
# When a conversation is escalated to a human (manual handoff, priority bump,
# AI signal), the agent picking up the Zoho ticket needs to know — at a
# glance — what the customer actually wants, WITHOUT reading the whole thread
# or opening Chatwoot. Before this, tickets showed only the bot's handoff
# line ("A teammate will follow up shortly"), which is useless context.
#
# Uses the same strict json_schema structured-output pattern as classifier.py
# so the result shape is guaranteed.

import json

import config
from llm_client import client

# Strict structured-output schema (OpenAI json_schema mode). Every property
# is required (strict-mode rule); empty strings mean "not determinable".
SUMMARY_RESPONSE_SCHEMA = {
    "name": "conversation_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["subject", "summary", "customer_goal", "next_step"],
        "properties": {
            # One-line ticket title — the customer's issue in <=10 words,
            # NEVER the bot's handoff phrasing.
            "subject":       {"type": "string"},
            # 1-3 sentence recap of the whole conversation so far.
            "summary":       {"type": "string"},
            # What the customer ultimately wants (the goal behind the messages).
            "customer_goal": {"type": "string"},
            # The single most useful thing the human agent should do next.
            "next_step":     {"type": "string"},
        },
    },
}

SUMMARY_SYSTEM_PROMPT = """\
You summarise a customer-support conversation for the human agent who is
about to take it over from a bot. Write for the AGENT, not the customer.

Rules:
- Focus on what the CUSTOMER wants and why they were escalated. IGNORE the
  bot's filler lines ("a teammate will follow up", "how can I help") —
  they are noise, never the subject.
- subject: the customer's core issue in <=10 words, plain and specific
  ("Double-charged for order #4521", "Wants refund, item never arrived").
  Never "Customer needs help" or the bot's handoff text.
- summary: 1-3 sentences recapping the conversation so far.
- customer_goal: the concrete outcome the customer is after.
- next_step: the most useful action for the agent to take next.
- Only use information actually present in the transcript. Do not invent
  order numbers, names, or facts. Use "" if a field isn't determinable.
"""

_SAFE_DEFAULT = {
    "subject": "",
    "summary": "",
    "customer_goal": "",
    "next_step": "",
}


def _format_transcript(messages: list[dict], limit: int = 40) -> str:
    """Render messages into a labelled transcript for the model. Keeps the
    last `limit` messages (most relevant) and drops empty/system rows."""
    lines = []
    for m in messages[-limit:]:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        # message_type: 0 incoming (customer), 1 outgoing (agent/bot).
        who = "Customer" if m.get("message_type") in (0, "incoming") \
            else config.AI_AGENT_NAME
        lines.append(f"{who}: {content}")
    return "\n".join(lines)


async def summarize_conversation(messages: list[dict]) -> dict:
    """Return {subject, summary, customer_goal, next_step}. Best-effort:
    returns _SAFE_DEFAULT (all empty) on no content or any failure, so the
    caller can fall back to its existing subject/transcript logic."""
    transcript = _format_transcript(messages)
    if not transcript.strip():
        return dict(_SAFE_DEFAULT)

    try:
        r = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            max_tokens=400,
            response_format={
                "type": "json_schema",
                "json_schema": SUMMARY_RESPONSE_SCHEMA,
            },
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": f"Conversation transcript:\n\n{transcript}"},
            ],
            name="ticket-conversation-summary",
            metadata={"langfuse_tags": ["summarizer", "zoho-ticket"]},
        )
        parsed = json.loads(r.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"[summarizer] failed: {type(e).__name__}: {e}")
        return dict(_SAFE_DEFAULT)

    # Strict schema guarantees the keys/types; trim for storage sanity.
    return {
        "subject":       (parsed.get("subject") or "")[:120],
        "summary":       (parsed.get("summary") or "")[:600],
        "customer_goal": (parsed.get("customer_goal") or "")[:300],
        "next_step":     (parsed.get("next_step") or "")[:300],
    }
