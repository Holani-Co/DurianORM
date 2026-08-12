# Shared Langfuse-instrumented OpenAI client.
#
# Importing the OpenAI SDK from `langfuse.openai` (instead of `openai`) is a
# drop-in: every chat/completions call is automatically traced to Langfuse as a
# generation — capturing model, prompt/response, and token usage — using the
# LANGFUSE_* env vars. `config` is imported first so .env (OpenAI + Langfuse
# keys) is loaded before the client is constructed.

import re

import config
from langfuse.openai import AsyncOpenAI

# One shared async client, reused across the service — including the reviews
# poller's detached background task. Nesting into a conversation trace is done
# by passing explicit ids to create() (see tracing.py), never OTel ambient
# context, so the wrapper is safe in detached tasks. (A previous
# `raw_client`/un-instrumented escape hatch existed to dodge an
# `async with propagate_attributes` error that has since been removed; it is no
# longer needed.)
client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

# GPT-5.6-family models default to MEDIUM reasoning on Chat Completions even
# when the param is absent — which would silently slow every legacy single-shot
# call (classifier, drafter, gates) after the Luna model swap, and break
# function-tool calls (tools + thinking need the Responses API). Pin
# reasoning_effort="none" here, in the one place every legacy call flows
# through, unless a caller explicitly overrides. gpt-4o-era models ignore this.
def floor_effort(model: str) -> str:
    """The no-thinking value for a model: gpt-5.4+ use 'none' (verified — 5.4
    rejects 'minimal'); the original gpt-5.0-5.3 series only go down to
    'minimal'."""
    m = re.match(r"gpt-(\d+)\.(\d+)", str(model or ""))
    if m and (int(m.group(1)), int(m.group(2))) >= (5, 4):
        return "none"
    return "minimal"


_orig_create = client.chat.completions.create


async def _create_with_reasoning_pin(*args, **kwargs):
    model = str(kwargs.get("model") or "")
    if model.startswith("gpt-5"):
        if "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = floor_effort(model)
        # gpt-5.x renamed max_tokens; translate so legacy call sites keep
        # working across the model swap.
        if "max_tokens" in kwargs and "max_completion_tokens" not in kwargs:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
    return await _orig_create(*args, **kwargs)


client.chat.completions.create = _create_with_reasoning_pin
