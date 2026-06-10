"""Streaming hook for amplifier-agent bundle.

Subscribes to foundation kernel hook events, translates them into typed
DisplayEvent shapes, and emits each via the display.emit capability
registered on the coordinator.

CANONICAL_WIRE_EVENTS defines the subset of the display taxonomy this
hook actively produces.  The kernel fires colon-separated events
(e.g. ``tool:pre``); this hook translates them into slash-separated
wire event types (e.g. ``tool/started``).

Kernel event schema notes (observed from amplifier-core ≥1.5):
- ``content_block:delta`` is NOT fired in the current kernel; text content
  arrives in ``content_block:end`` via the ``block`` dict field.
- ``llm:response`` carries no ``text`` field; token counts are nested under
  a ``usage`` sub-dict; the event is the completion signal for the turn.
- Block identity uses ``block_index`` (integer) rather than ``block_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from amplifier_core.models import HookResult

if TYPE_CHECKING:
    pass

CANONICAL_WIRE_EVENTS: tuple[str, ...] = (
    "result/delta",
    "result/final",
    "tool/started",
    "tool/completed",
    "usage",
)


def _block_id(data: dict[str, Any]) -> str:
    """Extract a stable per-block identifier from event data.

    Supports both the legacy ``block_id``/``index`` schema and the current
    ``block_index`` schema emitted by amplifier-core ≥1.5.
    """
    return data.get("block_id", "") or str(data.get("block_index", "") or data.get("index", ""))


def _parse_agent_name(session_id: str) -> str | None:
    """Extract the sub-agent name from a delegated session id.

    Session id format: ``{parent}-{child}_{agent_name}`` for delegated
    (sub-agent) sessions.  Root sessions contain no underscore and return
    ``None``.
    """
    if "_" not in session_id:
        return None
    name = session_id.split("_", 1)[1]
    return name or None


class StreamingEmitter:
    """Translates kernel hook events into DisplayEvent shapes and emits them.

    Instantiated once per ``mount()`` call and closed over the coordinator
    so that all handlers share the same block-level state dicts.
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        # Per-content-block state: block_id -> bool
        self._delta_seen: dict[str, bool] = {}
        # Per-content-block accumulated text (reserved for future use)
        self._block_text: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _emit(self, event: dict[str, Any]) -> None:
        """Emit a display event via the display.emit capability.

        No-ops silently if ``display.emit`` is not yet registered on the
        coordinator (e.g. during session initialisation before the app layer
        has had a chance to wire the capability).
        """
        fn = self._coordinator.get_capability("display.emit")
        if fn is not None:
            await fn(event)

    # ------------------------------------------------------------------
    # Hook handlers
    # ------------------------------------------------------------------

    async def on_tool_pre(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``tool:pre`` → wire ``tool/started``."""
        tool_name: str = data.get("tool") or data.get("tool_name") or ""
        tool_args: dict = data.get("arguments") or data.get("tool_input") or {}
        await self._emit(
            {
                "type": "tool/started",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "toolCallId": data.get("tool_call_id", ""),
                "name": tool_name,
                "args": tool_args,
            }
        )
        return HookResult(action="continue")

    async def on_tool_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``tool:post`` → wire ``tool/completed``."""
        tool_name: str = data.get("tool") or data.get("tool_name") or ""
        await self._emit(
            {
                "type": "tool/completed",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "toolCallId": data.get("tool_call_id", ""),
                "name": tool_name,
                "result": data.get("result"),
                "durationMs": int(data.get("duration_ms", 0)),
            }
        )
        return HookResult(action="continue")

    async def on_tool_error(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``tool:error`` → wire ``error``."""
        await self._emit(
            {
                "type": "error",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "code": data.get("error_code", "tool_failed") or "tool_failed",
                "message": data.get("error_message", ""),
                "recoverable": True,
            }
        )
        return HookResult(action="continue")

    async def on_content_block_start(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``content_block:start`` — initialise per-block state."""
        block_id: str = _block_id(data)
        self._delta_seen[block_id] = False
        self._block_text[block_id] = ""
        return HookResult(action="continue")

    async def on_content_block_delta(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``content_block:delta`` → wire ``result/delta``.

        Fired by some kernel versions during streaming; not fired in
        amplifier-core ≥1.5 (text arrives in ``content_block:end`` instead).
        Kept for forward-compat with older kernels.
        """
        block_id: str = _block_id(data)
        self._delta_seen[block_id] = True
        text: str = data.get("text", "")
        await self._emit(
            {
                "type": "result/delta",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "text": text,
            }
        )
        return HookResult(action="continue")

    async def on_content_block_end(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``content_block:end`` — emit fallback delta if none fired; cleanup state.

        In amplifier-core ≥1.5, ``content_block:delta`` is not fired; the
        complete block content arrives here in ``data["block"]`` as a dict
        with ``text`` and ``type`` keys.  This handler emits ``result/delta``
        from the block text for text-type blocks (not thinking/tool_use blocks).
        """
        block_id: str = _block_id(data)
        if not self._delta_seen.get(block_id, False):
            # Current kernel schema: text is in data["block"]["text"]
            # Legacy kernel schema: text is in data["text"]
            block_data = data.get("block", {})
            if isinstance(block_data, dict):
                text: str = block_data.get("text", "") or data.get("text", "")
                block_type: str = block_data.get("type", "text")
            else:
                text = data.get("text", "")
                block_type = "text"

            # Only emit result/delta for text-type blocks; skip thinking/tool_use.
            if text and block_type in ("text", ""):
                await self._emit(
                    {
                        "type": "result/delta",
                        "sessionId": data.get("session_id", ""),
                        "turnId": data.get("turn_id", ""),
                        "text": text,
                    }
                )
        # Cleanup block state
        self._delta_seen.pop(block_id, None)
        self._block_text.pop(block_id, None)
        return HookResult(action="continue")

    async def on_llm_response(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``llm:response`` → wire ``usage`` + ``result/final``.

        In amplifier-core ≥1.5, token counts are nested under a ``usage``
        sub-dict and there is no top-level ``text`` field (text arrives via
        ``content_block:end``).  This handler emits ``usage`` if token data is
        present and always emits ``result/final`` as the turn-completion signal.
        """
        session_id: str = data.get("session_id", "")
        turn_id: str = data.get("turn_id", "")

        # Token counts: check both top-level (legacy) and nested usage dict (current).
        usage_dict: dict[str, Any] = data.get("usage", {}) or {}
        in_tok: int = int(data.get("input_tokens", 0) or usage_dict.get("input_tokens", 0) or 0)
        out_tok: int = int(data.get("output_tokens", 0) or usage_dict.get("output_tokens", 0) or 0)

        # Text: present in legacy kernels only; empty string in current kernels
        # (text was already delivered via content_block:end events).
        text: str = data.get("text", "") or ""

        if in_tok or out_tok:
            usage_ev: dict[str, Any] = {
                "type": "usage",
                "sessionId": session_id,
                "turnId": turn_id,
                "inputTokens": in_tok,
                "outputTokens": out_tok,
            }
            # Enrichment — attach each field only when the kernel supplied it, to
            # respect the schema's additionalProperties:false + non-null typed slots.
            duration_ms = data.get("duration_ms")
            if duration_ms is not None:
                usage_ev["llmDurationMs"] = int(duration_ms)
            model = data.get("model")
            if model:
                usage_ev["model"] = str(model)
            provider = data.get("provider")
            if provider:
                usage_ev["provider"] = str(provider)
            cache_read = usage_dict.get("cache_read_tokens")
            if cache_read is not None:
                usage_ev["cacheReadTokens"] = int(cache_read)
            cache_write = usage_dict.get("cache_write_tokens")
            if cache_write is not None:
                usage_ev["cacheWriteTokens"] = int(cache_write)
            cost = usage_dict.get("cost_usd")
            if cost is not None:
                # Kernel serializes Decimal cost to a string; keep it a string to
                # preserve monetary precision on the wire.
                usage_ev["cost"] = str(cost)
            agent_name = _parse_agent_name(session_id)
            if agent_name is not None:
                usage_ev["agentName"] = agent_name
            await self._emit(usage_ev)

        # Always emit result/final as the turn-completion signal.
        # In current kernels the text field will be empty (text came earlier via
        # content_block:end), but the event itself is the canonical end-of-turn marker.
        await self._emit(
            {
                "type": "result/final",
                "sessionId": session_id,
                "turnId": turn_id,
                "text": text,
            }
        )
        return HookResult(action="continue")


async def mount(coordinator: Any, config: Any = None) -> None:
    """Mount the streaming hook on the coordinator.

    Instantiates a :class:`StreamingEmitter` and registers 7 handlers on
    ``coordinator.hooks`` covering:

    * ``tool:pre``
    * ``tool:post``
    * ``tool:error``
    * ``content_block:start``
    * ``content_block:delta``
    * ``content_block:end``
    * ``llm:response``
    """
    emitter = StreamingEmitter(coordinator)
    hooks = coordinator.hooks

    hooks.register("tool:pre", emitter.on_tool_pre, name="streaming_hook")
    hooks.register("tool:post", emitter.on_tool_post, name="streaming_hook")
    hooks.register("tool:error", emitter.on_tool_error, name="streaming_hook")
    hooks.register("content_block:start", emitter.on_content_block_start, name="streaming_hook")
    hooks.register("content_block:delta", emitter.on_content_block_delta, name="streaming_hook")
    hooks.register("content_block:end", emitter.on_content_block_end, name="streaming_hook")
    hooks.register("llm:response", emitter.on_llm_response, name="streaming_hook")
