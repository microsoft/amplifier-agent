"""Tests for protocol_points/defaults_stdio.py — StdioDisplaySystem."""

from __future__ import annotations

import asyncio
import json

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeWriter:
    """Minimal in-memory writer for testing JSON-RPC framing."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


# ---------------------------------------------------------------------------
# StdioDisplaySystem tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_writes_jsonrpc_notification() -> None:
    """emit() writes exactly one JSON-RPC notification line with correct shape."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import StdioDisplaySystem

    writer = _FakeWriter()
    system = StdioDisplaySystem(writer)
    await system.emit("result/delta", {"text": "Hello"})

    assert len(writer.written) == 1
    line = writer.written[0].rstrip(b"\n")
    msg = json.loads(line)
    assert msg["jsonrpc"] == "2.0"
    assert msg["method"] == "result/delta"
    assert msg["params"] == {"text": "Hello"}
    assert "id" not in msg


@pytest.mark.asyncio
async def test_emit_result_final_sets_tracking_flag() -> None:
    """emit('result/final', ...) sets result_final_emitted to True."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import StdioDisplaySystem

    writer = _FakeWriter()
    system = StdioDisplaySystem(writer)

    assert system.result_final_emitted is False
    await system.emit("result/final", {"text": "Done"})
    assert system.result_final_emitted is True


@pytest.mark.asyncio
async def test_reset_for_turn_clears_flag() -> None:
    """reset_for_turn() clears the result_final_emitted flag."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import StdioDisplaySystem

    writer = _FakeWriter()
    system = StdioDisplaySystem(writer)

    await system.emit("result/final", {"text": "Done"})
    assert system.result_final_emitted is True

    system.reset_for_turn()
    assert system.result_final_emitted is False


# ---------------------------------------------------------------------------
# StdioApprovalSystem tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_request_writes_then_waits_for_response() -> None:
    """Happy path: writes approval/request, resolves when handle_response called."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioApprovalSystem,
        StdioDisplaySystem,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)
    system = StdioApprovalSystem(writer, display=display)

    async def inject_response() -> None:
        # Yield once so request() can register its future before we resolve it
        await asyncio.sleep(0)
        request_msg = json.loads(writer.written[0])
        req_id = request_msg["id"]
        system.handle_response({"jsonrpc": "2.0", "id": req_id, "result": {"action": "accept"}})

    task = asyncio.create_task(inject_response())
    result = await system.request(kind="tool/call", payload={"tool": "shell"}, timeout_ms=5000)
    await task

    assert result == {"action": "accept"}

    # Verify the request was written with correct shape
    request_msg = json.loads(writer.written[0])
    assert request_msg["jsonrpc"] == "2.0"
    assert request_msg["method"] == "approval/request"
    assert request_msg["id"] == 1
    assert request_msg["params"] == {
        "kind": "tool/call",
        "payload": {"tool": "shell"},
        "timeoutMs": 5000,
    }


@pytest.mark.asyncio
async def test_approval_request_times_out_returns_cancel_and_emits_timeout_notification() -> None:
    """On timeout: returns cancel/timeout and emits approval/timeout notification."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioApprovalSystem,
        StdioDisplaySystem,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)
    system = StdioApprovalSystem(writer, display=display)

    result = await system.request(kind="tool/call", payload={}, timeout_ms=50)

    assert result == {"action": "cancel", "reason": "timeout"}

    # Two writes: approval/request then approval/timeout notification
    assert len(writer.written) == 2

    request_msg = json.loads(writer.written[0])
    assert request_msg["method"] == "approval/request"

    timeout_msg = json.loads(writer.written[1])
    assert timeout_msg["method"] == "approval/timeout"
    assert "id" not in timeout_msg  # notification — no id field


@pytest.mark.asyncio
async def test_approval_response_with_unknown_id_is_ignored() -> None:
    """handle_response with an unknown id silently ignores the message (no crash)."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioApprovalSystem,
        StdioDisplaySystem,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)
    system = StdioApprovalSystem(writer, display=display)

    # Should not raise even though no request is pending for id 999
    system.handle_response({"jsonrpc": "2.0", "id": 999, "result": {"action": "accept"}})


@pytest.mark.asyncio
async def test_approval_decline_action_passed_through() -> None:
    """handle_response with a decline action is returned as-is to the caller."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioApprovalSystem,
        StdioDisplaySystem,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)
    system = StdioApprovalSystem(writer, display=display)

    async def inject_decline() -> None:
        await asyncio.sleep(0)
        request_msg = json.loads(writer.written[0])
        req_id = request_msg["id"]
        system.handle_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"action": "decline", "reason": "user rejected"},
            }
        )

    task = asyncio.create_task(inject_decline())
    result = await system.request(kind="tool/call", payload={}, timeout_ms=5000)
    await task

    assert result == {"action": "decline", "reason": "user rejected"}
