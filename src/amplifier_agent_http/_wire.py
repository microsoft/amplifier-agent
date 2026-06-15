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
    stream: bool = False
    """Non-streaming requests are accepted but the POC always emits SSE
    internally and buffers if needed. opencode always streams."""

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


def stop_chunk(
    chunk_id: str,
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    include_usage: bool = True,
) -> dict[str, Any]:
    """Final chunk -- empty delta, finish_reason: stop, optional usage block.

    opencode's @ai-sdk/openai-compatible always passes include_usage=True, so
    omitting `usage` here will silently zero opencode's cost tracking. We always
    include it (zeros are fine in the POC)."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    if include_usage:
        chunk["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
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
