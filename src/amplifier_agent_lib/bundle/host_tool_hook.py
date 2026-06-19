"""Host-tool hook: emits OpenAI-shape tool_calls SSE chunks at tool:pre.

When the LLM picks a tool that opencode declared as host-side, the orchestrator
emits ``tool:pre`` with the tool name, tool_call_id, and the LLM's argument
dict. This hook intercepts that event, looks up whether the tool is in the
per-request host-tool set, and -- if it is -- pushes a ``tool_calls/delta``
display event so the HTTP face can translate it into a wire-shape SSE chunk
(``choices[0].delta.tool_calls[]``).

The actual loop exit happens in ``HostToolProxy.execute()`` immediately after
this hook returns: the proxy raises ``HostToolYield`` (BaseException), which
escapes loop-streaming's narrow ``except Exception`` guards and propagates to
the HTTP face. By that time, this hook has already populated the wire with
the tool_calls delta, so the HTTP face just needs to emit a terminal
``finish_reason: "tool_calls"`` chunk.

Why a hook AND a proxy (not just one)?
- The kernel's ``Tool`` protocol does not give the proxy easy access to
  tool_call_id or the display.emit capability. The hook receives both as part
  of the event payload and has the coordinator in closure.
- Splitting concerns also matches the kernel's mental model: hooks observe and
  shape control flow at lifecycle boundaries; tools are units of execution.
  The hook does the observation+wire-shape work; the proxy does the exit.

POC notes:
- The host-tool set is configured per-request via ``mount(..., config={"host_tools": [...]})``.
  The session runner calls ``mount()`` once per chat turn with the names of
  tools opencode declared in the request's ``tools[]``.
- The hook returns ``continue`` unconditionally. The wire-shape work is a
  side-effect, not a hook action. The proxy that runs next is what actually
  yields control.
- For non-host tools (e.g. bundle tools like ``delegate`` or ``todo``), the
  hook is a no-op pass-through.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from amplifier_core.models import HookResult

# Direct event-name literal -- amplifier_core.events.TOOL_PRE re-exports the
# string "tool:pre" from the Rust kernel, but its pyi stub doesn't always
# surface re-exports for static type-checkers. Using the literal sidesteps
# that without sacrificing correctness; the kernel matches on the string.
_TOOL_PRE_EVENT = "tool:pre"

logger = logging.getLogger(__name__)


# Display event type for host-tool calls. The HTTP face's _event_translator.py
# maps this onto OpenAI's choices[0].delta.tool_calls[] shape.
_HOST_TOOL_CALL_EVENT_TYPE = "tool_calls/delta"


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register the host-tool hook on this session's coordinator.

    Called once per chat turn from ``_session_runner.run_chat_turn`` AFTER the
    standard streaming hook is mounted (so display.emit is wired) and AFTER
    the per-request host-tool proxies are registered.

    Parameters
    ----------
    coordinator:
        The per-session coordinator instance returned by
        ``PreparedBundle.create_session``.
    config:
        ``host_tools``: list of tool-name strings that should be treated as
        host-side (opencode-delegated) rather than amplifier-side bundle tools.
        Empty or missing list disables the hook entirely (it still registers
        but always pass-through).

        ``yield_state``: optional dict the hook writes into when it fires for a
        host tool. The HTTP face passes this dict through ``run_chat_turn``
        so the stream generator can detect the host-tool yield WITHOUT relying
        on exception type preservation across the kernel's session.execute()
        bridge (which wraps BaseException subclasses as plain RuntimeError).
        Keys written: ``yielded`` (bool), ``tool_name`` (str), ``tool_call_id`` (str).

    Returns
    -------
    A small metadata dict so this mount call participates honestly in the
    kernel's protocol-compliance check.
    """
    config = config or {}
    host_tools = frozenset(config.get("host_tools") or [])
    yield_state = config.get("yield_state")

    if not host_tools:
        logger.debug("host_tool_hook mounted with empty host_tools list -- pass-through only")

    async def _on_tool_pre(event: str, data: dict[str, Any]) -> HookResult:
        """Emit a host-tool tool_calls delta if this event is for a host tool."""
        tool_name = data.get("tool_name", "")
        if tool_name not in host_tools:
            return HookResult(action="continue")

        tool_call_id = data.get("tool_call_id", "") or ""
        tool_input = data.get("tool_input")

        # The wire wants function.arguments as a JSON string. The kernel passes
        # the arguments as either a dict (Anthropic) or string (OpenAI raw).
        if isinstance(tool_input, str):
            arguments_str = tool_input
        elif tool_input is None:
            arguments_str = "{}"
        else:
            try:
                arguments_str = json.dumps(tool_input, separators=(",", ":"))
            except (TypeError, ValueError):
                arguments_str = "{}"

        # Push the host-tool delta event onto the per-request display queue.
        # The HTTP face will pull this from the queue and translate it into a
        # ``choices[0].delta.tool_calls[]`` SSE chunk.
        emit = coordinator.get_capability("display.emit") if hasattr(coordinator, "get_capability") else None
        if emit is None:
            # No display capability -- nothing to emit to. The proxy will still
            # raise HostToolYield, but the wire won't have the tool_calls delta.
            # This is a degraded state; log it loudly.
            logger.warning(
                "host_tool_hook: no display.emit capability registered; "
                "tool_calls delta for %r will be missing on the wire",
                tool_name,
            )
            return HookResult(action="continue")

        try:
            await emit(
                {
                    "type": _HOST_TOOL_CALL_EVENT_TYPE,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "arguments": arguments_str,
                    # POC: parallel index is always 0. Multi-tool parallel
                    # calls require coordinated index assignment across the
                    # asyncio.gather group; deferred to v2.
                    "index": 0,
                }
            )
        except Exception as exc:
            logger.warning(
                "host_tool_hook: display.emit raised for %r: %s",
                tool_name,
                exc,
            )
            # Continue so the proxy still raises HostToolYield -- wire shape
            # is degraded but the orchestrator exit still works.

        # Mark the yield in the shared state dict so the HTTP face can detect
        # this happened even if the kernel's session.execute() bridge wraps the
        # eventual HostToolYield BaseException into a plain RuntimeError (which
        # it does at amplifier-core python/amplifier_core/session.py:232-235:
        # the `raise` re-raises but PyO3 boundary does not preserve BaseException
        # subclass identity for callers awaiting from Python).
        if isinstance(yield_state, dict):
            yield_state["yielded"] = True
            yield_state["tool_name"] = tool_name
            yield_state["tool_call_id"] = tool_call_id

        return HookResult(action="continue")

    # Register the handler on the kernel hook bus. Priority 50 places this
    # AFTER the streaming hook's tool/started emit (priority 10 by default)
    # so the wire ordering puts the assistant's role marker and any prior
    # text deltas before the tool_calls delta. The "after" guarantee isn't
    # strictly required by the wire but reads more naturally.
    coordinator.hooks.register(
        event=_TOOL_PRE_EVENT,
        handler=_on_tool_pre,
        priority=50,
        name="host-tool-emit",
    )

    return {
        "name": "host-tool-hook",
        "version": "0.1.0",
        "host_tools_count": len(host_tools),
    }
