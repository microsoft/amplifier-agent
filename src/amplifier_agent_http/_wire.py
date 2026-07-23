"""OpenAI Chat Completions wire-shape types and helpers.

Pydantic v2 models for request/response and small helpers that assemble
SSE chunks. Keep this module thin -- it's the seam between OpenAI's wire
contract and the rest of the HTTP face.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Incoming request (subset of OpenAI Chat Completions)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """One message in the conversation history."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool", "developer"]
    """OpenAI role. We accept all standard roles; the POC handles system/user/
    assistant and treats tool messages as opaque conversation context."""

    content: str | list[dict[str, Any]] | None = None
    """String content for text messages; list of content blocks (vision, tool
    results) for multimodal/tool messages. POC focuses on string content but
    accepts list for forward-compat."""

    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ToolDefinition(BaseModel):
    """Host-provided tool definition. POC accepts but ignores these."""

    model_config = ConfigDict(extra="allow")

    type: Literal["function"]
    function: dict[str, Any]


class StreamOptions(BaseModel):
    """OpenAI stream options. opencode's @ai-sdk/openai-compatible sets
    `include_usage: true` for every request."""

    model_config = ConfigDict(extra="allow")

    include_usage: bool = False


class ChatCompletionRequest(BaseModel):
    """The body of POST /v1/chat/completions."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool | None = None
    """Explicit streaming flag.  ``True`` → SSE; ``False`` → single JSON body.
    ``None`` (field absent) is treated as ``True`` for backward compatibility
    so existing clients that omit the field continue to get SSE."""

    tools: list[ToolDefinition] | None = None
    """Host-provided tools. Accepted but ignored in the POC -- amplifier never
    emits finish_reason: tool_calls. See v2 backlog for host-tool delegation."""

    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stop: str | list[str] | None = None
    stream_options: StreamOptions | None = None
    user: str | None = None


# ---------------------------------------------------------------------------
# Outgoing SSE chunk builders
# ---------------------------------------------------------------------------


def new_chunk_id() -> str:
    """Stable per-response chunk id, OpenAI shape `chatcmpl-XXXXX`."""
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _base_chunk(chunk_id: str, model: str, *, created: int | None = None) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created if created is not None else int(time.time()),
        "model": model,
    }


def role_chunk(chunk_id: str, model: str) -> dict[str, Any]:
    """First chunk of a stream -- announces the assistant role with no content."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
    return chunk


def content_delta_chunk(chunk_id: str, model: str, content: str) -> dict[str, Any]:
    """A text-delta chunk."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    return chunk


def reasoning_delta_chunk(chunk_id: str, model: str, reasoning: str) -> dict[str, Any]:
    """A reasoning-delta chunk.

    Uses the ``delta.reasoning_content`` field popularized by DeepSeek (and
    consumed natively by Vercel's AI SDK as ``reasoning-delta`` events,
    which opencode's processor renders as collapsible reasoning blocks
    above the assistant text).

    Amplifier's extended-thinking output is surfaced through this channel
    so opencode users can see the model's reasoning without it
    contaminating the assistant text.
    """
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {"reasoning_content": reasoning}, "finish_reason": None}]
    return chunk


def _build_usage_block(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    cost_usd: str | None = None,
) -> dict[str, Any]:
    """Assemble the OpenAI usage block, with prompt_tokens_details when relevant.

    OpenAI's wire extension ``prompt_tokens_details.cached_tokens`` is how
    OpenAI-compatible clients (opencode included) see how many of the input
    tokens were served from cache (vs newly billed). Anthropic's prompt cache
    makes this distinction huge -- a turn with 19,000 cache_write tokens
    looks 1000x cheaper than reality when only ``input_tokens`` (the new,
    uncached portion) is forwarded.

    Always include the details object when there's usage at all -- clients
    that don't understand it ignore it; clients that do get accurate
    cache visibility.

    ``cost_usd`` is a non-standard amplifier-agent extension: the actual
    dollar cost the provider module computed for this turn, surfaced as a
    string to preserve Decimal precision on the wire. Standard OpenAI
    clients ignore unknown usage fields; cost-aware clients can render
    the real per-turn dollar value rather than computing it themselves
    from per-million catalog rates. Omitted when ``None`` (e.g. providers
    that don't emit ``cost_usd`` in their llm:response events).
    """
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if prompt_tokens or completion_tokens:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    if cost_usd is not None:
        usage["cost_usd"] = cost_usd
    return usage


def stop_chunk(
    chunk_id: str,
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    cost_usd: str | None = None,
    total_nano_aiu: int | None = None,
    include_usage: bool = True,
) -> dict[str, Any]:
    """Final chunk -- empty delta, finish_reason: stop, optional usage block.

    opencode's @ai-sdk/openai-compatible always passes include_usage=True, so
    omitting `usage` here will silently zero opencode's cost tracking. We always
    include it.

    ``cached_tokens`` is surfaced under ``usage.prompt_tokens_details.cached_tokens``
    per the OpenAI usage-block extension. Pass the Anthropic
    ``cache_read_input_tokens`` count here so cost tracking on the consumer
    side reflects the actual cache hit rate.

    ``cost_usd`` is the actual dollar cost provider modules computed for
    this turn -- surfaced as a string (Decimal precision) on
    ``usage.cost_usd`` (non-standard extension; standard clients ignore).
    """
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    if include_usage:
        chunk["usage"] = _build_usage_block(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
        )
        if total_nano_aiu is not None:
            chunk["copilot_usage"] = {"total_nano_aiu": total_nano_aiu}
    return chunk


def tool_call_delta_chunk(
    chunk_id: str,
    model: str,
    *,
    index: int,
    tool_call_id: str,
    name: str,
    arguments: str,
) -> dict[str, Any]:
    """A tool-call delta chunk.

    Matches the OpenAI Chat Completions tool-call streaming shape consumed by
    @ai-sdk/openai-compatible: the first chunk for a given tool call carries
    `id`, `type`, `function.name`, and starts `function.arguments`; subsequent
    chunks may extend `function.arguments`. For the POC we always emit the full
    arguments JSON in a single chunk -- per-fragment streaming of the JSON body
    would require provider-side support that the Anthropic provider does not
    currently surface as a hook event (the SDK's input_json_delta is not
    re-emitted on the kernel hook bus).

    `arguments` MUST be a JSON-serialized string (not a dict) per OpenAI's wire.
    """
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [
        {
            "index": 0,
            "delta": {
                "tool_calls": [
                    {
                        "index": index,
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    }
                ],
            },
            "finish_reason": None,
        }
    ]
    return chunk


def tool_calls_stop_chunk(
    chunk_id: str,
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    cost_usd: str | None = None,
    total_nano_aiu: int | None = None,
    include_usage: bool = True,
) -> dict[str, Any]:
    """Terminal chunk for a turn that ends with host-delegated tool calls.

    Like ``stop_chunk`` but with ``finish_reason: "tool_calls"`` rather than
    ``"stop"``. This is the signal opencode's @ai-sdk/openai-compatible adapter
    watches for to know "the model wants to call tools, run them host-side and
    re-POST with the results."

    ``cached_tokens`` is surfaced under ``usage.prompt_tokens_details.cached_tokens``
    in the same shape ``stop_chunk`` uses -- pass through the Anthropic
    ``cache_read_input_tokens`` count for accurate consumer-side cost tracking.

    ``cost_usd`` -- non-standard amplifier-agent extension -- carries the
    actual dollar cost the provider module computed for this turn.
    """
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
    if include_usage:
        chunk["usage"] = _build_usage_block(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
        )
        if total_nano_aiu is not None:
            chunk["copilot_usage"] = {"total_nano_aiu": total_nano_aiu}
    return chunk


def sse_data(chunk: dict[str, Any]) -> str:
    """Serialize a chunk dict to a single SSE `data:` event."""
    return f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"


def sse_done() -> str:
    """Terminal SSE marker required by the OpenAI spec."""
    return "data: [DONE]\n\n"


def sse_keepalive() -> str:
    """SSE comment line -- not delivered to the client app, but keeps proxies
    and the underlying HTTP connection alive during silent gaps."""
    return ": keepalive\n\n"
