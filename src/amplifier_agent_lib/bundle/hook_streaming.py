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

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from amplifier_core.models import HookResult

if TYPE_CHECKING:
    pass

CANONICAL_WIRE_EVENTS: tuple[str, ...] = (
    "result/delta",
    "result/final",
    "tool/started",
    "tool/completed",
    "thinking/delta",
    "thinking/final",
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


def _sum_cost_usd(results: list[dict[str, Any]]) -> str | None:
    """Sum ``cost_usd`` contributions, preserving Decimal precision.

    Replicated inline (not imported) from
    ``amplifier_foundation.bundle._prepared.sum_cost_usd`` to keep this hook
    free of foundation coupling.  Contributions carry cost as a string (the
    kernel's Decimal-as-string convention).  Returns the total as a string, or
    ``None`` when no contributor reported a cost.
    """
    total: Decimal | None = None
    for entry in results:
        raw = entry.get("cost_usd")
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        # Decimal("NaN") and Decimal("Infinity") are valid Decimals that do NOT
        # raise InvalidOperation. Skip them — a single NaN would poison the sum
        # and emit "sessionCostTotal": "NaN" on the wire, silently breaking any
        # budget enforcement consumer.
        if not value.is_finite():
            continue
        total = value if total is None else total + value
    return str(total) if total is not None else None


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
        session_id: str = data.get("session_id", "")
        ev: dict[str, Any] = {
            "type": "tool/started",
            "sessionId": session_id,
            "turnId": data.get("turn_id", ""),
            "toolCallId": data.get("tool_call_id", ""),
            "name": tool_name,
            "args": tool_args,
        }
        agent_name = _parse_agent_name(session_id)
        if agent_name is not None:
            ev["agentName"] = agent_name
        await self._emit(ev)
        return HookResult(action="continue")

    async def on_tool_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``tool:post`` → wire ``tool/completed``."""
        tool_name: str = data.get("tool") or data.get("tool_name") or ""
        session_id: str = data.get("session_id", "")
        ev: dict[str, Any] = {
            "type": "tool/completed",
            "sessionId": session_id,
            "turnId": data.get("turn_id", ""),
            "toolCallId": data.get("tool_call_id", ""),
            "name": tool_name,
            "result": data.get("result"),
            "durationMs": int(data.get("duration_ms", 0)),
        }
        agent_name = _parse_agent_name(session_id)
        if agent_name is not None:
            ev["agentName"] = agent_name
        await self._emit(ev)
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

            # Emit result/delta for text-type blocks; emit thinking/delta for
            # thinking-type blocks. tool_use blocks remain unsurfaced (they're
            # described separately via tool/started + tool/completed events).
            #
            # Surfacing thinking is opt-in for hosts: CliDisplaySystem
            # suppresses thinking/delta at default verbosity (see
            # protocol_points/defaults_cli.py:_SUPPRESSED_AT_DEFAULT), so the
            # CLI face is unchanged. The HTTP face routes thinking/delta into
            # OpenAI's `delta.reasoning_content` so opencode renders it as a
            # collapsible reasoning block above the assistant text.
            if text and block_type in ("text", ""):
                await self._emit(
                    {
                        "type": "result/delta",
                        "sessionId": data.get("session_id", ""),
                        "turnId": data.get("turn_id", ""),
                        "text": text,
                    }
                )
            elif text and block_type == "thinking":
                await self._emit(
                    {
                        "type": "thinking/delta",
                        "sessionId": data.get("session_id", ""),
                        "turnId": data.get("turn_id", ""),
                        "text": text,
                    }
                )
        # Cleanup block state
        self._delta_seen.pop(block_id, None)
        self._block_text.pop(block_id, None)
        return HookResult(action="continue")

    async def on_thinking_delta(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``thinking:delta`` → wire ``thinking/delta``."""
        await self._emit(
            {
                "type": "thinking/delta",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "text": data.get("text", "") or "",
            }
        )
        return HookResult(action="continue")

    async def on_thinking_final(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``thinking:final`` → wire ``thinking/final``.

        Reads ``data['text']`` when present; otherwise falls back to
        ``data['block']['text']`` (the current kernel delivers completed blocks
        in a ``block`` sub-dict, mirroring ``content_block:end``).
        """
        text: str = data.get("text", "") or ""
        if not text:
            block = data.get("block", {})
            if isinstance(block, dict):
                text = block.get("text", "") or ""
        await self._emit(
            {
                "type": "thinking/final",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "text": text,
            }
        )
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

    async def on_orchestrator_complete(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``orchestrator:complete`` → wire session-total ``usage``.

        Collects per-call ``session.cost`` contributions from the coordinator
        and emits a single ``usage`` event carrying ``sessionCostTotal``.  Token
        counts are zero on this rollup event (the required schema fields are
        satisfied; the meaningful payload is the cost total).

        Note: ``sessionCostTotal`` reflects what ``collect_contributions``
        returns, which may differ from summing per-call ``cost`` fields.
        Sub-agent sessions can report higher totals due to how the kernel
        accumulates contributions across the coordinator hierarchy.  This is
        a kernel concern (`bridge_child_cost` semantics in foundation), not a
        bug in this hook.
        """
        collect = getattr(self._coordinator, "collect_contributions", None)
        if collect is None:
            return HookResult(action="continue")
        # collect_contributions is async in the real kernel; mocks must match.
        results = (await collect("session.cost")) or []
        total = _sum_cost_usd(results)
        if total is not None:
            await self._emit(
                {
                    "type": "usage",
                    "sessionId": data.get("session_id", ""),
                    "turnId": data.get("turn_id", ""),
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "sessionCostTotal": total,
                }
            )
        return HookResult(action="continue")


async def mount(coordinator: Any, config: Any = None) -> None:
    """Mount the streaming hook on the coordinator.

    Instantiates a :class:`StreamingEmitter` and registers 10 handlers on
    ``coordinator.hooks`` covering:

    * ``tool:pre``
    * ``tool:post``
    * ``tool:error``
    * ``content_block:start``
    * ``content_block:delta``
    * ``content_block:end``
    * ``llm:response``
    * ``thinking:delta``
    * ``thinking:final``
    * ``orchestrator:complete``
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
    hooks.register("thinking:delta", emitter.on_thinking_delta, name="streaming_hook")
    hooks.register("thinking:final", emitter.on_thinking_final, name="streaming_hook")
    hooks.register("orchestrator:complete", emitter.on_orchestrator_complete, name="streaming_hook")
