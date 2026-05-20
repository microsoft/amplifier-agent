"""Tests for Mode B stdio_loop — dispatch, shutdown, approval routing, L14 wiring (Task 7).

TDD RED phase: these tests exercise the NEW dispatch behaviours added in Task 7.
They MUST FAIL with NotImplementedError (or be skipped by the stub) before the
full implementation is in place, and PASS once stdio_loop.py is fully wired.

Test matrix (4 tests):
  1. test_turn_submit_with_engine_emitting_final_does_not_synthesize
     — engine emits result/final naturally → verify only 1 final, no synthesized marker

  2. test_turn_submit_with_engine_omitting_final_triggers_synthesis
     — engine omits → synthesized notification fires with synthesized:true
       AND the synthesized notification appears BEFORE the response on wire

  3. test_agent_shutdown_responds_and_exits
     — agent/shutdown → exit_code 0, shutdown_called True, response has 'result'
       (no EOF needed; shutdown itself causes the loop to exit)

  4. test_approval_response_routes_to_approval_system
     — turn callback calls engine.approval.request → writes approval/request;
       concurrent task watches writer buffer, feeds back response via reader.feed;
       verify engine receives accept action; EOF fed after submit resolves.

Helpers (reuse the same _PipeReader/_PipeWriter pattern as Task 6 tests):
  _PipeReader  — asyncio.Queue-backed reader with feed() / feed_eof() / readline()
  _PipeWriter  — in-memory writer; .messages() returns list[dict] of parsed lines
  _ScriptedEngine — takes on_turn_submit callback; scripts dispatch behavior
  _initialize_msg(id_) — returns a valid agent/initialize request dict
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _PipeReader:
    """Asyncio.Queue-backed reader.

    ``feed(msg)`` serialises a dict as NDJSON and enqueues it.
    ``feed_eof()`` enqueues the empty-bytes EOF sentinel.
    ``readline()`` dequeues the next item (async).
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, message: dict) -> None:  # type: ignore[type-arg]
        """Enqueue *message* as a serialised NDJSON line."""
        line = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        self._queue.put_nowait(line)

    def feed_eof(self) -> None:
        """Enqueue the EOF sentinel (empty bytes)."""
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        """Return the next enqueued item."""
        return await self._queue.get()


class _PipeWriter:
    """In-memory writer that collects written bytes.

    ``.messages()`` returns a list of dicts parsed from written NDJSON lines.
    """

    def __init__(self) -> None:
        self._written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self._written.append(data)

    async def drain(self) -> None:
        pass

    def messages(self) -> list[dict]:  # type: ignore[type-arg]
        """Return all written lines parsed as JSON objects."""
        return [json.loads(line.rstrip(b"\n")) for line in self._written]


class _ScriptedEngine:
    """Scripted engine for Task 7 dispatch tests.

    Takes an on_turn_submit callback and scripts dispatch behavior:
      - turn/submit → calls on_turn_submit callback (or returns default reply)
      - agent/shutdown → sets shutdown_called = True, returns {}
    """

    def __init__(
        self,
        on_turn_submit: Any = None,
    ) -> None:
        self.display: Any = None
        self.approval: Any = None
        self.shutdown_called: bool = False
        self._on_turn_submit = on_turn_submit

    def attach_display(self, display: Any) -> None:
        self.display = display

    def attach_approval(self, approval: Any) -> None:
        self.approval = approval

    async def initialize(
        self,
        *,
        client_capabilities: Any,
        client_info: Any,
    ) -> dict:  # type: ignore[type-arg]
        """Return a minimal valid initialize result."""
        return {
            "protocolVersion": "1.0.0",
            "serverInfo": {"name": "amplifier-agent", "version": "0.0.0"},
            "capabilities": client_capabilities,
        }

    async def dispatch(self, method: str, params: Any) -> Any:
        """Script dispatch based on method."""
        if method == "agent/shutdown":
            self.shutdown_called = True
            return {}
        if method == "turn/submit":
            if self._on_turn_submit is not None:
                return await self._on_turn_submit(params)
            return {"reply": "hello", "turnId": params.get("turnId", "t1")}
        raise ValueError(f"unknown method in scripted engine: {method!r}")


def _initialize_msg(id_: int = 1) -> dict:  # type: ignore[type-arg]
    """Build a valid agent/initialize request dict."""
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "agent/initialize",
        "params": {
            "capabilities": {"approval": True, "display": {"streaming": True}},
            "clientInfo": {"name": "test-host", "version": "0.1.0"},
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_submit_with_engine_emitting_final_does_not_synthesize() -> None:
    """Engine emits result/final naturally — verify only 1 final, no synthesized marker.

    When the engine callback emits result/final via display.emit() before
    returning, the L14 safety-net must NOT emit a second, synthesized notification.
    """
    from amplifier_agent_cli.modes import stdio_loop

    # We need the engine reference inside the callback; use a list as a mutable cell.
    engine_cell: list[_ScriptedEngine] = []

    async def emit_and_return(params: dict) -> dict:  # type: ignore[type-arg]
        """Emit result/final naturally, then return reply."""
        engine = engine_cell[0]
        turn_id = params.get("turnId", "t1")
        await engine.display.emit("result/final", {"text": "hello", "turnId": turn_id})
        return {"reply": "hello", "turnId": turn_id}

    engine = _ScriptedEngine(on_turn_submit=emit_and_return)
    engine_cell.append(engine)

    reader = _PipeReader()
    writer = _PipeWriter()

    reader.feed(_initialize_msg())
    reader.feed(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "turn/submit",
            "params": {"sessionId": "s1", "turnId": "t1", "prompt": "hello"},
        }
    )
    reader.feed_eof()

    exit_code = await stdio_loop.run(reader=reader, writer=writer, engine=engine)

    assert exit_code == 0

    msgs = writer.messages()
    finals = [m for m in msgs if m.get("method") == "result/final"]
    assert len(finals) == 1, f"Expected exactly 1 result/final, got {len(finals)}: {finals}"

    # Engine emitted naturally — no synthesized marker
    params_map = finals[0].get("params", {})
    assert "synthesized" not in params_map, (
        f"Should not have synthesized marker when engine emits naturally; got params: {params_map}"
    )


@pytest.mark.asyncio
async def test_turn_submit_with_engine_omitting_final_triggers_synthesis() -> None:
    """Engine omits result/final — verify synthesis fires with synthesized:true
    and the synthesized notification appears BEFORE the response on the wire.
    """
    from amplifier_agent_cli.modes import stdio_loop

    async def no_final_return(params: dict) -> dict:  # type: ignore[type-arg]
        """Return without emitting result/final."""
        return {"reply": "hello", "turnId": params.get("turnId", "t1")}

    engine = _ScriptedEngine(on_turn_submit=no_final_return)
    reader = _PipeReader()
    writer = _PipeWriter()

    reader.feed(_initialize_msg())
    reader.feed(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "turn/submit",
            "params": {"sessionId": "s1", "turnId": "t1", "prompt": "hello"},
        }
    )
    reader.feed_eof()

    exit_code = await stdio_loop.run(reader=reader, writer=writer, engine=engine)

    assert exit_code == 0

    msgs = writer.messages()

    # Locate result/final notifications and the turn/submit response
    finals = [(i, m) for i, m in enumerate(msgs) if m.get("method") == "result/final"]
    responses = [(i, m) for i, m in enumerate(msgs) if "result" in m and m.get("id") == 2]

    assert len(finals) == 1, f"Expected exactly 1 result/final (synthesized), got {len(finals)}: {finals}"
    assert len(responses) == 1, f"Expected 1 turn/submit response (id=2), got {len(responses)}"

    final_idx, final_msg = finals[0]
    response_idx, _response_msg = responses[0]

    # synthesized notification MUST appear BEFORE the response on wire
    assert final_idx < response_idx, (
        f"Synthesized result/final at index {final_idx} must appear BEFORE turn/submit response at index {response_idx}"
    )

    # Must carry synthesized:true debug marker (Appendix B)
    assert final_msg.get("params", {}).get("synthesized") is True, (
        f"Synthesized result/final must carry synthesized:true marker; got params: {final_msg.get('params')}"
    )


@pytest.mark.asyncio
async def test_agent_shutdown_responds_and_exits() -> None:
    """No EOF needed — shutdown causes exit, exit_code 0, shutdown_called True,
    response contains 'result'.
    """
    from amplifier_agent_cli.modes import stdio_loop

    engine = _ScriptedEngine()
    reader = _PipeReader()
    writer = _PipeWriter()

    reader.feed(_initialize_msg())
    reader.feed(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "agent/shutdown",
            "params": {},
        }
    )
    # No EOF fed — shutdown itself causes the loop to return 0

    exit_code = await stdio_loop.run(reader=reader, writer=writer, engine=engine)

    assert exit_code == 0, f"Expected exit_code 0, got {exit_code}"
    assert engine.shutdown_called is True, "engine.shutdown_called must be True after agent/shutdown"

    msgs = writer.messages()
    shutdown_responses = [m for m in msgs if m.get("id") == 2 and "result" in m]
    assert len(shutdown_responses) == 1, (
        f"Expected 1 shutdown response, got {len(shutdown_responses)}: {shutdown_responses}"
    )
    assert "error" not in shutdown_responses[0], (
        f"Shutdown response must not have 'error' key; got: {shutdown_responses[0]}"
    )


@pytest.mark.asyncio
async def test_approval_response_routes_to_approval_system() -> None:
    """Bidirectional approval routing end-to-end through reader/writer.

    Flow:
    1. turn callback calls engine.approval.request → writes approval/request to wire
    2. concurrent watcher task sees the approval/request in writer buffer
    3. watcher feeds the approval response (accept) via reader.feed
    4. loop reads the response and routes it to approval.handle_response
    5. approval future resolves → callback receives {action: 'accept'}
    6. turn/submit response written to wire
    7. watcher feeds EOF → loop exits cleanly
    """
    from amplifier_agent_cli.modes import stdio_loop

    received_action: list[str] = []
    engine_cell: list[_ScriptedEngine] = []

    async def turn_with_approval(params: dict) -> dict:  # type: ignore[type-arg]
        """Request approval and capture the received action."""
        engine = engine_cell[0]
        result = await engine.approval.request(
            kind="tool/call",
            payload={"tool": "bash"},
            timeout_ms=5000,
        )
        received_action.append(result.get("action", ""))
        return {"reply": "done", "turnId": params.get("turnId", "t1")}

    engine = _ScriptedEngine(on_turn_submit=turn_with_approval)
    engine_cell.append(engine)

    reader = _PipeReader()
    writer = _PipeWriter()

    reader.feed(_initialize_msg())
    reader.feed(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "turn/submit",
            "params": {"sessionId": "s1", "turnId": "t1", "prompt": "hello"},
        }
    )
    # EOF is fed AFTER the turn/submit response appears (by the watcher below)

    async def watcher() -> None:
        """Watch writer buffer; respond to approval/request, then feed EOF after turn response."""
        # Phase 1: wait for approval/request to appear in writer buffer
        while True:
            await asyncio.sleep(0.01)
            msgs = writer.messages()
            approval_reqs = [m for m in msgs if m.get("method") == "approval/request"]
            if approval_reqs:
                req_id = approval_reqs[0]["id"]
                # Feed the approval response back via reader
                reader.feed(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"action": "accept"},
                    }
                )
                break

        # Phase 2: wait for the turn/submit response to appear, then feed EOF
        while True:
            await asyncio.sleep(0.01)
            msgs = writer.messages()
            turn_responses = [m for m in msgs if m.get("id") == 2 and "result" in m]
            if turn_responses:
                reader.feed_eof()
                break

    # Run the loop and the watcher concurrently
    loop_task = asyncio.create_task(stdio_loop.run(reader=reader, writer=writer, engine=engine))
    await watcher()
    exit_code = await loop_task

    assert exit_code == 0, f"Expected exit_code 0, got {exit_code}"
    assert received_action == ["accept"], f"Expected engine to receive 'accept' action, got {received_action}"


@pytest.mark.asyncio
async def test_idle_timeout_triggers_exit_with_error_notification() -> None:
    """Idle timeout fires error notification and exits cleanly.

    When only agent/initialize is received and no further messages arrive
    within idle_timeout_s seconds, the loop emits exactly one error
    notification with method='error' and params.code='idle_timeout',
    then exits with exit_code 0.
    """
    from amplifier_agent_cli.modes import stdio_loop

    engine = _ScriptedEngine()
    reader = _PipeReader()
    writer = _PipeWriter()

    # Feed only the initialize message — no further messages will arrive.
    reader.feed(_initialize_msg(1))

    # Run with a very short idle timeout so the test completes quickly.
    exit_code = await stdio_loop.run(reader=reader, writer=writer, engine=engine, idle_timeout_s=0.1)

    assert exit_code == 0, f"Expected exit_code 0, got {exit_code}"

    msgs = writer.messages()

    # Exactly one error notification with code='idle_timeout' must be present.
    error_notifications = [
        m for m in msgs if m.get("method") == "error" and m.get("params", {}).get("code") == "idle_timeout"
    ]
    assert len(error_notifications) == 1, (
        f"Expected exactly 1 error notification with code='idle_timeout', "
        f"got {len(error_notifications)}: {error_notifications}\n"
        f"All messages: {msgs}"
    )
