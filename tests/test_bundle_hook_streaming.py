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


# ---------------------------------------------------------------------------
# Sub-cycle 11B: tool:post -> tool/completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_post_emits_tool_completed() -> None:
    """on_tool_post emits type='tool/completed' with name, toolCallId, result, durationMs."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-3",
        "turn_id": "turn-3",
        "tool_call_id": "call-def",
        "tool": "bash",
        "result": {"stdout": "file.txt"},
        "duration_ms": 42,
    }
    result = await emitter.on_tool_post("tool:post", data)

    assert result.action == "continue"
    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "tool/completed"
    assert ev["sessionId"] == "sess-3"
    assert ev["turnId"] == "turn-3"
    assert ev["toolCallId"] == "call-def"
    assert ev["name"] == "bash"
    assert ev["result"] == {"stdout": "file.txt"}
    assert ev["durationMs"] == 42


# ---------------------------------------------------------------------------
# Sub-cycle 11C: content_block handlers (result/delta + fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_block_delta_emits_result_delta() -> None:
    """on_start then on_delta with text='Hello' emits one result/delta with text='Hello'."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    block_data = {
        "session_id": "sess-4",
        "turn_id": "turn-4",
        "block_id": "block-1",
    }
    await emitter.on_content_block_start("content_block:start", block_data)
    assert len(coord.emitted) == 0  # start emits nothing

    delta_data = {
        "session_id": "sess-4",
        "turn_id": "turn-4",
        "block_id": "block-1",
        "text": "Hello",
    }
    result = await emitter.on_content_block_delta("content_block:delta", delta_data)

    assert result.action == "continue"
    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "result/delta"
    assert ev["sessionId"] == "sess-4"
    assert ev["turnId"] == "turn-4"
    assert ev["text"] == "Hello"


@pytest.mark.asyncio
async def test_content_block_end_fallback_when_no_delta_fired() -> None:
    """on_start then on_end with text='Full text' (no delta) emits exactly one fallback result/delta."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    start_data = {
        "session_id": "sess-5",
        "turn_id": "turn-5",
        "block_id": "block-2",
    }
    await emitter.on_content_block_start("content_block:start", start_data)

    end_data = {
        "session_id": "sess-5",
        "turn_id": "turn-5",
        "block_id": "block-2",
        "text": "Full text",
    }
    result = await emitter.on_content_block_end("content_block:end", end_data)

    assert result.action == "continue"
    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "result/delta"
    assert ev["text"] == "Full text"


@pytest.mark.asyncio
async def test_content_block_end_skips_fallback_when_delta_fired() -> None:
    """start, delta('chunk-1'), end emits only the real delta (no fallback on end)."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    block_id = "block-3"
    base = {"session_id": "sess-6", "turn_id": "turn-6", "block_id": block_id}

    await emitter.on_content_block_start("content_block:start", base)
    await emitter.on_content_block_delta("content_block:delta", {**base, "text": "chunk-1"})

    emitted_before = list(coord.emitted)
    await emitter.on_content_block_end("content_block:end", {**base, "text": "chunk-1"})

    # No extra emission from on_end
    assert len(coord.emitted) == len(emitted_before)


@pytest.mark.asyncio
async def test_content_block_end_cleans_up_state() -> None:
    """on_end cleans up _delta_seen and _block_text for the block."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    block_id = "block-cleanup"
    base = {"session_id": "s", "turn_id": "t", "block_id": block_id}

    await emitter.on_content_block_start("content_block:start", base)
    assert block_id in emitter._delta_seen

    await emitter.on_content_block_end("content_block:end", {**base, "text": ""})

    assert block_id not in emitter._delta_seen
    assert block_id not in emitter._block_text


# ---------------------------------------------------------------------------
# Sub-cycle 11D: llm:response -> usage + result/final
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_response_emits_usage_and_result_final() -> None:
    """llm:response emits 'usage' and 'result/final' with correct fields."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-7",
        "turn_id": "turn-7",
        "text": "the full reply",
        "input_tokens": 100,
        "output_tokens": 50,
    }
    result = await emitter.on_llm_response("llm:response", data)

    assert result.action == "continue"
    assert len(coord.emitted) == 2

    emitted_types = [ev["type"] for ev in coord.emitted]
    assert "usage" in emitted_types
    assert "result/final" in emitted_types

    usage_ev = next(ev for ev in coord.emitted if ev["type"] == "usage")
    assert usage_ev["inputTokens"] == 100
    assert usage_ev["outputTokens"] == 50

    final_ev = next(ev for ev in coord.emitted if ev["type"] == "result/final")
    assert final_ev["text"] == "the full reply"


@pytest.mark.asyncio
async def test_llm_response_no_usage_when_zero_tokens() -> None:
    """llm:response with zero tokens emits only result/final (no usage event)."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-8",
        "turn_id": "turn-8",
        "text": "reply only",
    }
    await emitter.on_llm_response("llm:response", data)

    emitted_types = [ev["type"] for ev in coord.emitted]
    assert "usage" not in emitted_types
    assert "result/final" in emitted_types


@pytest.mark.asyncio
async def test_llm_response_final_emitted_with_empty_text() -> None:
    """llm:response always emits result/final as turn-completion signal, even with empty text.

    In amplifier-core ≥1.5, llm:response carries no text field (text arrives
    via content_block:end events).  result/final is the canonical end-of-turn
    marker and must be emitted regardless of whether text is present.
    """
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-9",
        "turn_id": "turn-9",
        "input_tokens": 10,
        "output_tokens": 5,
        "text": "",
    }
    await emitter.on_llm_response("llm:response", data)

    emitted_types = [ev["type"] for ev in coord.emitted]
    assert "usage" in emitted_types
    # result/final is always emitted: it is the turn-completion signal.
    assert "result/final" in emitted_types
    final_ev = next(ev for ev in coord.emitted if ev["type"] == "result/final")
    assert final_ev["text"] == ""


# ---------------------------------------------------------------------------
# Sub-cycle 11E: tool:error -> error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_emits_error_event() -> None:
    """on_tool_error emits type='error' with code, message, recoverable=True."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "sess-10",
        "turn_id": "turn-10",
        "tool": "bash",
        "error_code": "tool_failed",
        "error_message": "exit code 1",
    }
    result = await emitter.on_tool_error("tool:error", data)

    assert result.action == "continue"
    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "error"
    assert ev["sessionId"] == "sess-10"
    assert ev["turnId"] == "turn-10"
    assert ev["code"] == "tool_failed"
    assert ev["message"] == "exit code 1"
    assert ev["recoverable"] is True


@pytest.mark.asyncio
async def test_tool_error_defaults_code_to_tool_failed() -> None:
    """on_tool_error falls back to 'tool_failed' when error_code is absent."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "s",
        "turn_id": "t",
        "error_message": "something broke",
    }
    await emitter.on_tool_error("tool:error", data)

    assert len(coord.emitted) == 1
    assert coord.emitted[0]["code"] == "tool_failed"


# ---------------------------------------------------------------------------
# Sub-cycle 11F: _parse_agent_name helper
# ---------------------------------------------------------------------------


def test_parse_agent_name_extracts_sub_agent() -> None:
    """A delegated session id ({parent}-{child}_{agent}) yields the agent name."""
    from amplifier_agent_lib.bundle.hook_streaming import _parse_agent_name

    assert _parse_agent_name("abc123-def456_explorer") == "explorer"
    assert _parse_agent_name("0000-1111_superpowers-plan-writer") == "superpowers-plan-writer"


def test_parse_agent_name_returns_none_for_root_session() -> None:
    """A root session id (no underscore) yields None."""
    from amplifier_agent_lib.bundle.hook_streaming import _parse_agent_name

    assert _parse_agent_name("abc123-def456") is None
    assert _parse_agent_name("") is None


@pytest.mark.asyncio
async def test_llm_response_usage_includes_enrichment_fields() -> None:
    """on_llm_response attaches duration, model, provider, cache tokens, and cost to usage."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "root-1_explorer",
        "turn_id": "turn-1",
        "text": "",
        "duration_ms": 3200,
        "model": "claude-opus-4-20250514",
        "provider": "anthropic",
        "usage": {
            "input_tokens": 1247,
            "output_tokens": 892,
            "cache_read_tokens": 600,
            "cache_write_tokens": 47,
            "cost_usd": "0.0142",
        },
    }
    await emitter.on_llm_response("llm:response", data)

    usage_ev = next(ev for ev in coord.emitted if ev["type"] == "usage")
    assert usage_ev["inputTokens"] == 1247
    assert usage_ev["outputTokens"] == 892
    assert usage_ev["llmDurationMs"] == 3200
    assert usage_ev["model"] == "claude-opus-4-20250514"
    assert usage_ev["provider"] == "anthropic"
    assert usage_ev["cacheReadTokens"] == 600
    assert usage_ev["cacheWriteTokens"] == 47
    assert usage_ev["cost"] == "0.0142"  # string, not float
    assert isinstance(usage_ev["cost"], str)
    assert usage_ev["agentName"] == "explorer"


@pytest.mark.asyncio
async def test_llm_response_omits_absent_enrichment_fields() -> None:
    """Enrichment fields absent from kernel data are NOT attached (no None values)."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "root-1",  # root session: no agentName
        "turn_id": "turn-1",
        "input_tokens": 10,
        "output_tokens": 5,
    }
    await emitter.on_llm_response("llm:response", data)

    usage_ev = next(ev for ev in coord.emitted if ev["type"] == "usage")
    for absent in ("llmDurationMs", "model", "provider", "cacheReadTokens", "cacheWriteTokens", "cost", "agentName"):
        assert absent not in usage_ev, f"{absent} should be omitted when source is absent"
