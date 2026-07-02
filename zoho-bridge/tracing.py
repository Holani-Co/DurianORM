# Conversation-scoped Langfuse trace hierarchy.
#
# The team wants every Langfuse trace to correspond to ONE conversation, with a
# child span per message and every LLM action nested under its message:
#
#   Trace  conversation:<conv_id>   tags=[conversation:<conv_id>, source:<host>]  session=<conv_id>
#    ├─ span  message:<message_id>
#    │   ├─ generation  email-type-classification
#    │   ├─ generation  email-12-category-classification
#    │   └─ generation  team-classification
#    └─ span  message:<other_id>
#        └─ generation  document-extraction
#
# Because a single conversation spans MANY separate webhook calls (each message,
# status change, etc. is its own HTTP event, sometimes days apart and across
# service restarts), the trace id is DETERMINISTIC — seeded from conv_id — so
# every webhook for a conversation lands in the same trace.
#
# Nesting is done with EXPLICIT ids: each OpenAI call is passed the conversation
# trace_id + the message span id as `trace_id` / `parent_observation_id`. We
# deliberately AVOID OTel ambient-context nesting (propagate_attributes /
# start_as_current_observation): the reviews poller and document extraction run
# in detached asyncio tasks where OTel ambient-context propagation is
# unreliable, whereas linking by id is a synchronous, context-free code path
# that works everywhere.
#
# The message span is created, stamped with the trace-level attributes, and
# ended IMMEDIATELY — Langfuse links children to a parent purely by id (via a
# NonRecordingSpan), so the parent need not stay open. This sidesteps span
# lifecycle management across the handlers' many early returns; the span is a
# ~0-duration grouping node whose children carry the real timing.
#
# Usage:
#     lf = tracing.message_parent(conv_id, message_id, event="message_created")
#     await classifier.classify_email_type(content, ..., lf_parent=lf)
#
# Every traced helper takes an optional `lf_parent` dict and does
# `await client.chat.completions.create(..., **(lf_parent or {}))`. When it is
# empty/None the call is traced exactly as before (flat), so tracing is fully
# opt-in per call site and never changes behaviour on failure.

import config
from langfuse import get_client
from langfuse._client.attributes import LangfuseOtelSpanAttributes as _Attr

_lf = get_client()

# Which running instance produced this trace (prod ORM server vs a dev laptop).
# Constant for the process lifetime — see config.TRACE_SOURCE.
_SOURCE_TAG = f"source:{config.TRACE_SOURCE}"


def conversation_trace_id(conv_id) -> str:
    """Deterministic 32-hex Langfuse trace id for a conversation. The same
    conv_id always maps to the same trace, across webhooks and restarts."""
    return _lf.create_trace_id(seed=f"conversation:{conv_id}")


def message_parent(conv_id, message_id=None, *, name=None, **metadata) -> dict:
    """Open (and immediately close) the message/action span under the
    conversation trace, returning the ``{trace_id, parent_observation_id}`` dict
    to splat into every OpenAI call for this message.

    Non-message actions (a status-change summary, say) pass message_id=None and
    a descriptive `name`. Best-effort: returns ``{}`` (untraced) when conv_id is
    missing or on any tracing error, so a tracing hiccup never breaks the
    pipeline."""
    if conv_id is None:
        return {}
    try:
        trace_id = conversation_trace_id(conv_id)
        label = name or (f"message:{message_id}" if message_id is not None else "action")
        span = _lf.start_observation(
            as_type="span",
            name=label,
            trace_context={"trace_id": trace_id},
            metadata={"conversation_id": conv_id, "message_id": message_id, **metadata},
        )
        # This span is AS_ROOT for the trace (created from a trace_context with
        # no parent), so trace-level attributes set here apply to the whole
        # conversation trace: name it, group it as a session, tag the conv id.
        otel = span._otel_span
        otel.set_attribute(_Attr.TRACE_NAME, f"conversation:{conv_id}")
        otel.set_attribute(_Attr.TRACE_SESSION_ID, str(conv_id))
        otel.set_attribute(_Attr.TRACE_TAGS, [f"conversation:{conv_id}", _SOURCE_TAG])
        span.end()
        return {"trace_id": span.trace_id, "parent_observation_id": span.id}
    except Exception as e:  # tracing must never break the pipeline
        print(f"[tracing] message_parent failed ({type(e).__name__}): {e}")
        return {}


def event(conv_id, name, *, parent: dict = None, output=None, **metadata) -> None:
    """Record a point-in-time DECISION or automated ACTION in the conversation
    trace — the things LLM generations don't capture: auto-snooze, team
    assignment, auto-posting a Google reply, Zoho ticket creation, rating-only
    template picks (which make no LLM call at all), and so on.

    `output` carries the decision payload (shows as the observation's output in
    Langfuse). Nested under `parent` (a message_parent dict) when given, else
    attached directly under the conversation trace. Best-effort: never raises,
    so recording a decision can never break the decision."""
    try:
        if parent and parent.get("trace_id"):
            ctx = {"trace_id": parent["trace_id"],
                   "parent_span_id": parent.get("parent_observation_id")}
        elif conv_id is not None:
            ctx = {"trace_id": conversation_trace_id(conv_id)}
        else:
            return
        _lf.start_observation(
            as_type="event",
            name=name,
            trace_context=ctx,
            output=output,
            metadata={"conversation_id": conv_id, **metadata} or None,
        ).end()
    except Exception as e:
        print(f"[tracing] event failed ({type(e).__name__}): {e}")
