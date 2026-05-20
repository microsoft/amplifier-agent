"""Tests for src/amplifier_agent_lib/bundle/hook_streaming.py.

Five TDD sub-cycles:
  11A: tool:pre -> tool/started
  11B: tool:post -> tool/completed
  11C: content_block handlers (delta + fallback)
  11D: llm:response -> usage + result/final
  11E: tool:error -> error
"""

from __future__ import annotations

import pytest

from amplifier_agent_lib.bundle.hook_streaming import (
    CANONICAL_WIRE_EVENTS,
    StreamingEmitter,
    mount,
)


# ---------------------------------------------------------------------------
# Test infrastructure — mock coordinator
# ---------------------------------------------------------------------------


class _MockHooks:
    """Records calls to register()."""

    def __init__(self) -> None:
        self.registered: list[tuple[str, object, str | None]] = []

    def register(self, event: str, handler: object, *, name: str | None = None, priority: int = 0) -> None:
        self.registered.append((event, handler, name))


class _MockCoordinator:
    """Mock coordinator that captures emitted display events."""

    def __init__(self) -> None:
        self.emitted: list[dict] = []
        self.hooks = _MockHooks()

    def get_capability(self, name: str) -> object:
        if name == "display.emit":
            async def _emit(event: dict) -> None:
                self.emitted.append(event)
            return _emit
        raise KeyError(f"Unknown capability: {name!r}")


# ---------------------------------------------------------------------------
# Sanity: CANONICAL_WIRE_EVENTS constant
# ---------------------------------------------------------------------------


def test_canonical_wire_events_contains_required_types() -> None:
    """CANONICAL_WIRE_EVENTS must contain the 5 required wire event types."""
    required = {"result/delta", "result/final", "tool/started", "tool/completed", "usage"}
    assert required == set(CANONICAL_WIRE_EVENTS)


# ---------------------------------------------------------------------------
# mount() registers 7 handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mount_registers_seven_handlers() -> None:
    """mount() must register exactly 7 handlers on coordinator.hooks."""
    coord = _MockCoordinator()
    await mount(coord)

    registered_events = [evt for evt, _, _ in coord.hooks.registered]
    expected_events = {
        "tool:pre",
        "tool:post",
        "tool:error",
        "content_block:start",
        "content_block:delta",
        "content_block:end",
        "llm:response",
    }
    assert len(coord.hooks.registered) == 7
    assert set(registered_events) == expected_events


# ---------------------------------------------------------------------------
# Sub-cycle 11A: tool:pre -> tool/started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_pre_emits_tool_started() -> None:
    """on_tool_pre emits type='tool/started' with sessionId, turnId, name, toolCallId, args."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-1",
        "turn_id": "turn-1",
        "tool_call_id": "call-abc",
        "tool": "bash",
        "arguments": {"cmd": "ls"},
    }
    result = await emitter.on_tool_pre("tool:pre", data)

    assert result.action == "continue"
    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "tool/started"
    assert ev["sessionId"] == "sess-1"
    assert ev["turnId"] == "turn-1"
    assert ev["toolCallId"] == "call-abc"
    assert ev["name"] == "bash"
    assert ev["args"] == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_tool_pre_defensive_tool_name_field() -> None:
    """on_tool_pre reads 'tool_name' when 'tool' is absent; reads 'tool_input' as args."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-2",
        "turn_id": "turn-2",
        "tool_call_id": "call-xyz",
        "tool_name": "filesystem",
        "tool_input": {"path": "/x"},
    }
    result = await emitter.on_tool_pre("tool:pre", data)

    assert result.action == "continue"
    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "tool/started"
    assert ev["name"] == "filesystem"
    assert ev["args"] == {"path": "/x"}
