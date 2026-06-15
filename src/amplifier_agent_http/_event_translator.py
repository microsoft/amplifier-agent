"""Translate kernel display events to OpenAI SSE chunks.

The kernel emits a handful of display event types via the streaming hook
(see ``amplifier_agent_lib.bundle.hook_streaming``). The HTTP face filters
and reshapes these for the OpenAI Chat Completions wire.

POC rule -- "internal stays internal":
- ``result/delta`` text emerges as ``choices[0].delta.content``
- ``tool/started`` and ``tool/completed`` are DROPPED -- opencode sees no
  internal tool activity (matches Ollama / llama.cpp pattern)
- ``error`` is logged and surfaced as a text content delta with a prefix
  so the user sees something useful in the TUI
- ``usage`` is captured by the runner for the final chunk's ``usage`` block
  (not emitted as a separate chunk)
- ``result/final`` is informational and dropped -- the final stop chunk is
  emitted by the runner when execute() returns
- ``thinking/*``, ``progress`` -- dropped (not visible on OpenAI wire)

If a new event type appears that isn't in the table, log it once at INFO
and drop it. This keeps the wire stable when the kernel adds new event
shapes; we opt into surfacing them deliberately rather than leaking through.
"""

from __future__ import annotations

import logging
from typing import Any

from amplifier_agent_http._wire import content_delta_chunk, reasoning_delta_chunk
from amplifier_agent_lib.protocol_points.base import DisplayEvent

logger = logging.getLogger(__name__)


# Event types that exist but produce no SSE chunk in the POC translation.
# We track them so we can log unknown event types only once each.
#
# Slice 3 update: thinking/delta is no longer dropped -- it's translated to
# delta.reasoning_content so opencode can render it as a collapsible
# reasoning block above the assistant text. thinking/final remains dropped
# because the corresponding text already flowed through thinking/delta.
_DROPPED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "tool/started",
        "tool/completed",
        "result/final",
        "thinking/final",
        "progress",
        "usage",  # consumed by the runner for the final chunk's usage block
    }
)


def translate_event(
    event: DisplayEvent,
    chunk_id: str,
    model_id: str,
    seen_unknown_types: set[str],
) -> dict[str, Any] | None:
    """Translate one display event into a chunk dict, or None to drop it.

    ``seen_unknown_types`` is a caller-owned set so we can log each novel
    event type once across the lifetime of a request (or longer, if the
    caller chooses to share the set across requests).
    """
    event_type = event.get("type", "")

    if event_type == "result/delta":
        # Primary text streaming event. ``text`` is the delta string.
        text = event.get("text", "")
        if isinstance(text, str) and text:
            return content_delta_chunk(chunk_id, model_id, text)
        return None

    if event_type == "thinking/delta":
        # Extended-thinking text. Surfaced via OpenAI's reasoning_content
        # channel so opencode renders it separately from assistant content.
        # No fallback to delta.content -- mixing reasoning into content
        # would pollute conversation history for the next turn.
        text = event.get("text", "")
        if isinstance(text, str) and text:
            return reasoning_delta_chunk(chunk_id, model_id, text)
        return None

    if event_type == "error":
        # Surface errors as inline text so the TUI shows something. The full
        # error envelope translation (with proper HTTP status mapping) is in
        # the v2 backlog -- for the POC we keep the stream healthy and just
        # tell the user what went wrong.
        code = event.get("code", "")
        message = event.get("message", "Unknown error")
        text = f"\n\n[amplifier-agent error: {code} {message}]\n".strip() + "\n"
        return content_delta_chunk(chunk_id, model_id, text)

    if event_type in _DROPPED_EVENT_TYPES:
        return None

    # Unknown event type. Log once per request (cheap, useful for dogfood
    # debugging) and drop.
    if event_type not in seen_unknown_types:
        seen_unknown_types.add(event_type)
        logger.info("dropping unknown display event type=%r (logged once per request)", event_type)
    return None


def extract_usage(event: DisplayEvent) -> dict[str, int] | None:
    """If the event is a usage event, extract token counts in OpenAI shape.

    Returns ``None`` for non-usage events so the caller can use a simple
    accumulator: ``if (u := extract_usage(ev)): usage_block = u``.

    The kernel emits ``usage`` events with ``inputTokens`` / ``outputTokens``.
    OpenAI's wire uses ``prompt_tokens`` / ``completion_tokens``. We rename.

    The POC accumulates the LAST usage event in the turn. If the bundle does
    multiple internal LLM calls and emits multiple usage events, we end up
    reporting just the final one. Proper aggregation across internal LLM
    calls is in the v2 backlog.
    """
    if event.get("type") != "usage":
        return None
    input_tokens = event.get("inputTokens", 0)
    output_tokens = event.get("outputTokens", 0)
    try:
        prompt = int(input_tokens) if input_tokens is not None else 0
        completion = int(output_tokens) if output_tokens is not None else 0
    except (TypeError, ValueError):
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
