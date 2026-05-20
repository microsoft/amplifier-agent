"""Streaming hook for amplifier-agent bundle.

Subscribes to foundation kernel hook events, translates them into typed
DisplayEvent shapes, and emits each via the display.emit capability
registered on the coordinator.

CANONICAL_WIRE_EVENTS defines the subset of the display taxonomy this
hook actively produces.  The kernel fires colon-separated events
(e.g. ``tool:pre``); this hook translates them into slash-separated
wire event types (e.g. ``tool/started``).
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
        """Emit a display event via the display.emit capability."""
        fn = self._coordinator.get_capability("display.emit")
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
        return HookResult(action="continue")

    async def on_tool_error(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``tool:error`` → wire ``error``."""
        return HookResult(action="continue")

    async def on_content_block_start(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``content_block:start`` — initialise per-block state."""
        return HookResult(action="continue")

    async def on_content_block_delta(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``content_block:delta`` → wire ``result/delta``."""
        return HookResult(action="continue")

    async def on_content_block_end(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``content_block:end`` — emit fallback delta if none fired; cleanup state."""
        return HookResult(action="continue")

    async def on_llm_response(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``llm:response`` → wire ``usage`` + ``result/final``."""
        return HookResult(action="continue")


async def mount(coordinator: Any, config: Any = None) -> None:  # noqa: ARG001
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
