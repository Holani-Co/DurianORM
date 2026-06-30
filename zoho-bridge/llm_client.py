# Shared Langfuse-instrumented OpenAI client.
#
# Importing the OpenAI SDK from `langfuse.openai` (instead of `openai`) is a
# drop-in: every chat/completions call is automatically traced to Langfuse as a
# generation — capturing model, prompt/response, and token usage — using the
# LANGFUSE_* env vars. `config` is imported first so .env (OpenAI + Langfuse
# keys) is loaded before the client is constructed.

import config
from langfuse.openai import AsyncOpenAI
from openai import AsyncOpenAI as _PlainAsyncOpenAI

# One shared async client, reused across the service.
client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

# Un-instrumented client for calls made OUTSIDE a request context — notably the
# reviews poller's detached background task (asyncio.create_task). There, the
# Langfuse/OpenTelemetry async tracing wrapper raises
#   TypeError: '_AgnosticContextManager' object does not support the
#   asynchronous context manager protocol
# which made every review fall back (drafting → canned template, positivity
# check → forced handoff). The plain OpenAI client has no tracing wrapper, so
# those calls succeed. Trade-off: no Langfuse traces for these specific calls.
raw_client = _PlainAsyncOpenAI(api_key=config.OPENAI_API_KEY)
