# Shared Langfuse-instrumented OpenAI client.
#
# Importing the OpenAI SDK from `langfuse.openai` (instead of `openai`) is a
# drop-in: every chat/completions call is automatically traced to Langfuse as a
# generation — capturing model, prompt/response, and token usage — using the
# LANGFUSE_* env vars. `config` is imported first so .env (OpenAI + Langfuse
# keys) is loaded before the client is constructed.

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
