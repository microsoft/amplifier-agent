"""Tests for Mode B stdio_loop — capability handshake (Task 6).

TDD RED phase: these tests must FAIL with ModuleNotFoundError (or ImportError)
before stdio_loop.py is created, then PASS once the module is implemented.

Test matrix (2 tests):
  1. test_handshake_returns_capabilities_on_first_initialize
     — agent/initialize first → response has protocolVersion "1.0.0",
       serverInfo.name "amplifier-agent", engine receives client capabilities.

  2. test_handshake_rejects_non_initialize_first_request
     — turn/submit first → JSON-RPC error code -32002, data.code "agent_not_ready".

Helpers (in-process, no subprocess):
  _PipeReader  — asyncio.Queue-backed reader with feed() / feed_eof() / readline()
  _PipeWriter  — in-memory writer; .messages() returns list[dict] of parsed lines
  _FakeEngine  — minimal stand-in: attach_display / attach_approval / initialize / dispatch
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


class _FakeEngine:
    """Minimal engine stand-in for testing the stdio handshake.

    Tracks which protocol points were attached and what initialize() was called
    with, so tests can assert on them.
    """

    def __init__(self) -> None:
        self.display: Any = None
        self.approval: Any = None
        self._init_kwargs: dict[str, Any] | None = None

    def attach_display(self, display: Any) -> None:
        self.display = display

    def attach_approval(self, approval: Any) -> None:
        self.approval = approval

    async def initialize(self, *, client_capabilities: Any, client_info: Any) -> dict:  # type: ignore[type-arg]
        """Store call kwargs, return a minimal valid initialize result."""
        self._init_kwargs = {
            "client_capabilities": client_capabilities,
            "client_info": client_info,
        }
        return {
            "protocolVersion": "1.0.0",
            "serverInfo": {"name": "amplifier-agent", "version": "0.0.0"},
            "capabilities": client_capabilities,
        }

    async def dispatch(self, method: str, params: Any) -> Any:
        raise NotImplementedError(f"dispatch not wired in Task 6: {method!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_returns_capabilities_on_first_initialize() -> None:
    """agent/initialize as the first request returns a valid handshake response.

    The response result MUST carry:
    - protocolVersion == "1.0.0"
    - serverInfo.name == "amplifier-agent"
    - capabilities (echoed from client in _FakeEngine)

    The engine MUST have been called with client_capabilities from params.
    """
    from amplifier_agent_cli.modes import stdio_loop

    reader = _PipeReader()
    writer = _PipeWriter()
    engine = _FakeEngine()

    client_caps = {"approval": True, "display": {"streaming": True}}
    reader.feed(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "agent/initialize",
            "params": {
                "capabilities": client_caps,
                "clientInfo": {"name": "test-host", "version": "0.1.0"},
            },
        }
    )
    reader.feed_eof()

    exit_code = await stdio_loop.run(reader=reader, writer=writer, engine=engine)

    assert exit_code == 0

    msgs = writer.messages()
    assert len(msgs) == 1, f"Expected exactly 1 response, got {len(msgs)}: {msgs}"

    response = msgs[0]
    assert response.get("jsonrpc") == "2.0"
    assert response.get("id") == 1
    assert "result" in response, f"Expected 'result' key in response, got: {response}"
    assert "error" not in response

    result = response["result"]
    assert result["protocolVersion"] == "1.0.0", (
        f"Expected protocolVersion '1.0.0', got {result.get('protocolVersion')!r}"
    )
    assert result["serverInfo"]["name"] == "amplifier-agent", (
        f"Expected serverInfo.name 'amplifier-agent', got {result.get('serverInfo', {}).get('name')!r}"
    )
    assert "capabilities" in result

    # Engine must have been called with the client capabilities and clientInfo
    assert engine._init_kwargs is not None, "engine.initialize() was never called"
    assert engine._init_kwargs["client_capabilities"] == client_caps
    assert engine._init_kwargs["client_info"] == {"name": "test-host", "version": "0.1.0"}


@pytest.mark.asyncio
async def test_handshake_rejects_non_initialize_first_request() -> None:
    """Any request other than agent/initialize before initialization returns agent_not_ready.

    Error shape:
    - JSON-RPC error code -32002
    - message "Engine not initialized"
    - data.code == "agent_not_ready"

    The engine's initialize() must NOT have been called.
    """
    from amplifier_agent_cli.modes import stdio_loop

    reader = _PipeReader()
    writer = _PipeWriter()
    engine = _FakeEngine()

    reader.feed(
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "turn/submit",
            "params": {"sessionId": "s1", "turnId": "t1", "prompt": "hello"},
        }
    )
    reader.feed_eof()

    exit_code = await stdio_loop.run(reader=reader, writer=writer, engine=engine)

    assert exit_code == 0

    msgs = writer.messages()
    assert len(msgs) == 1, f"Expected exactly 1 response, got {len(msgs)}: {msgs}"

    response = msgs[0]
    assert response.get("jsonrpc") == "2.0"
    assert response.get("id") == 42
    assert "error" in response, f"Expected 'error' key in response, got: {response}"
    assert "result" not in response

    error = response["error"]
    assert error["code"] == -32002, f"Expected error code -32002, got {error.get('code')!r}"
    assert "data" in error, f"Expected 'data' key in error, got: {error}"
    assert error["data"]["code"] == "agent_not_ready", (
        f"Expected data.code 'agent_not_ready', got {error['data'].get('code')!r}"
    )

    # Engine initialize() must NOT have been called
    assert engine._init_kwargs is None, "engine.initialize() must NOT be called before handshake"
