"""Tests for jsonrpc.py — NDJSON JSON-RPC 2.0 framing.

TDD RED phase: these tests must FAIL with ModuleNotFoundError before
jsonrpc.py is created, then PASS once the module is implemented.
"""

from __future__ import annotations

import asyncio
import json

import pytest


class MockWriter:
    """In-memory async writer for testing write_message."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.drain_called = 0

    def write(self, data: bytes) -> None:
        self._buffer.extend(data)

    async def drain(self) -> None:
        self.drain_called += 1

    @property
    def written(self) -> bytes:
        return bytes(self._buffer)


class MockReader:
    """In-memory async reader for testing read_message, backed by a queue of lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for line in lines:
            self._queue.put_nowait(line)
        # EOF sentinel — empty bytes signals end of stream
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


@pytest.mark.asyncio
async def test_write_message_appends_newline_and_serializes_json() -> None:
    """write_message must serialize msg, write UTF-8 bytes that end with \\n and contain exactly one \\n."""
    from amplifier_agent_lib.jsonrpc import write_message

    writer = MockWriter()
    msg = {"jsonrpc": "2.0", "method": "test", "id": 1}
    await write_message(writer, msg)

    data = writer.written
    # Output must end with \n
    assert data.endswith(b"\n"), f"Expected output to end with newline, got: {data!r}"
    # Exactly one newline in the output (NDJSON: one object per line)
    assert data.count(b"\n") == 1, f"Expected exactly one newline, got: {data.count(b'\\n')} in {data!r}"
    # Must parse back to the original message
    parsed = json.loads(data.decode("utf-8"))
    assert parsed == msg, f"Round-trip failed: {parsed!r} != {msg!r}"
    # drain() must have been called (for proper async flushing)
    assert writer.drain_called == 1, "drain() must be called exactly once"


@pytest.mark.asyncio
async def test_read_message_parses_one_line() -> None:
    """read_message must read one newline-terminated JSON object and return a dict."""
    from amplifier_agent_lib.jsonrpc import read_message

    msg = {"jsonrpc": "2.0", "method": "ping", "id": 42}
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"

    reader = MockReader([line])
    result = await read_message(reader)

    assert result == msg, f"Expected {msg!r}, got {result!r}"


@pytest.mark.asyncio
async def test_read_message_returns_none_on_eof() -> None:
    """read_message must return None when the stream is empty (EOF)."""
    from amplifier_agent_lib.jsonrpc import read_message

    reader = MockReader([])  # No lines — goes straight to EOF sentinel
    result = await read_message(reader)

    assert result is None, f"Expected None on EOF, got {result!r}"


@pytest.mark.asyncio
async def test_read_message_skips_malformed_lines() -> None:
    """read_message must skip non-JSON lines and continue reading."""
    from amplifier_agent_lib.jsonrpc import read_message

    good_msg = {"jsonrpc": "2.0", "id": 1, "result": "ok"}
    good_line = json.dumps(good_msg, separators=(",", ":")).encode("utf-8") + b"\n"
    junk_line = b"this is not json\n"

    reader = MockReader([junk_line, good_line])
    result = await read_message(reader)

    assert result == good_msg, f"Expected good message after skipping junk, got {result!r}"


@pytest.mark.asyncio
async def test_read_message_skips_non_object_json() -> None:
    """read_message must skip JSON arrays and scalars (non-dict JSON)."""
    from amplifier_agent_lib.jsonrpc import read_message

    good_msg = {"jsonrpc": "2.0", "id": 2, "result": "done"}
    array_line = b"[1,2,3]\n"
    scalar_line = b'"just a string"\n'
    good_line = json.dumps(good_msg, separators=(",", ":")).encode("utf-8") + b"\n"

    reader = MockReader([array_line, scalar_line, good_line])
    result = await read_message(reader)

    assert result == good_msg, f"Expected good message after skipping non-objects, got {result!r}"


@pytest.mark.asyncio
async def test_read_message_skips_non_json_lines() -> None:
    """read_message skips garbage lines mixed with valid JSON and returns the valid message."""
    from amplifier_agent_lib.jsonrpc import read_message

    good_msg = {"jsonrpc": "2.0", "method": "notify", "params": {}}
    garbage_line = b"WARNING: sub-process printed to stdout\n"
    good_line = json.dumps(good_msg, separators=(",", ":")).encode("utf-8") + b"\n"

    reader = MockReader([garbage_line, good_line])
    result = await read_message(reader)

    assert result == good_msg, f"Expected good message after skipping garbage, got {result!r}"


def test_classify_request() -> None:
    """classify returns 'request' for messages with both 'method' and 'id'."""
    from amplifier_agent_lib.jsonrpc import classify

    msg = {"jsonrpc": "2.0", "method": "tools/call", "id": 7, "params": {}}
    assert classify(msg) == "request"


def test_classify_response_result() -> None:
    """classify returns 'response' for messages with 'id' and 'result'."""
    from amplifier_agent_lib.jsonrpc import classify

    msg = {"jsonrpc": "2.0", "id": 7, "result": {"content": []}}
    assert classify(msg) == "response"


def test_classify_response_error() -> None:
    """classify returns 'response' for messages with 'id' and 'error'."""
    from amplifier_agent_lib.jsonrpc import classify

    msg = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32600, "message": "Invalid Request"}}
    assert classify(msg) == "response"


def test_classify_notification() -> None:
    """classify returns 'notification' for messages with 'method' but no 'id'."""
    from amplifier_agent_lib.jsonrpc import classify

    msg = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}}
    assert classify(msg) == "notification"


def test_classify_invalid() -> None:
    """classify returns 'invalid' for messages that match no canonical shape."""
    from amplifier_agent_lib.jsonrpc import classify

    assert classify({}) == "invalid"
    assert classify({"jsonrpc": "2.0"}) == "invalid"
    assert classify({"id": 1}) == "invalid"


def test_make_response_result() -> None:
    """make_response returns a well-formed JSON-RPC 2.0 result response."""
    from amplifier_agent_lib.jsonrpc import make_response

    resp = make_response(id=1, result={"status": "ok"})
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}


def test_make_error_with_data() -> None:
    """make_error returns a well-formed JSON-RPC 2.0 error response including data when provided."""
    from amplifier_agent_lib.jsonrpc import make_error

    # With data
    err = make_error(id=1, code=-32600, message="Invalid Request", data={"code": "wire_protocol_violation"})
    assert err == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32600,
            "message": "Invalid Request",
            "data": {"code": "wire_protocol_violation"},
        },
    }

    # Without data — 'data' key must be absent
    err_no_data = make_error(id=None, code=-32603, message="Internal error")
    assert err_no_data == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32603,
            "message": "Internal error",
        },
    }
    assert "data" not in err_no_data["error"]


def test_make_notification() -> None:
    """make_notification returns a well-formed JSON-RPC 2.0 notification (no 'id' field)."""
    from amplifier_agent_lib.jsonrpc import make_notification

    notif = make_notification(method="notifications/progress", params={"progress": 50})
    assert notif == {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progress": 50},
    }
    assert "id" not in notif
