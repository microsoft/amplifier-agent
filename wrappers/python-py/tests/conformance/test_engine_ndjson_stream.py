"""Conformance — engine stderr NDJSON wire-event parsing.

Drives ``parse_ndjson_stream`` against canonical wire-event sequences the
engine emits on stderr when invoked with ``--display ndjson``.  Verifies the
parser's contract for every wire-event method documented in the protocol
schemas, plus framing edge cases (blank lines, non-JSON intermixed with
JSON, JSON-but-not-object).

These are the wrapper-level counterpart to the JSON-RPC wire fixtures under
``wrappers/conformance/`` — both wrappers (TS and Python) must produce the
same notification stream for each canonical engine NDJSON output.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any

import pytest

from amplifier_agent_py import parse_ndjson_stream

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _stream_from_bytes(payload: bytes) -> asyncio.StreamReader:
    """Wrap a byte buffer in an ``asyncio.StreamReader`` for parse_ndjson_stream."""
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


async def _run(coro: Awaitable[None]) -> None:
    """Pump a single coroutine to completion on a fresh event loop."""
    await coro


def _drain(payload: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Drive parse_ndjson_stream against ``payload`` and return (json_lines, non_json_lines)."""
    json_lines: list[dict[str, Any]] = []
    non_json_lines: list[str] = []

    async def go() -> None:
        await parse_ndjson_stream(
            _stream_from_bytes(payload.encode("utf-8")),
            on_json=json_lines.append,
            on_non_json=non_json_lines.append,
        )

    asyncio.run(go())
    return json_lines, non_json_lines


# ---------------------------------------------------------------------------
# Canonical wire-event method coverage
#
# Each test sends a single wire notification with the canonical schema shape
# (from src/amplifier_agent_lib/protocol/schemas/*.schema.json) and verifies
# the parser surfaces it verbatim to on_json.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, params",
    [
        ("progress", {"sessionId": "s", "turnId": "t", "message": "thinking"}),
        ("result/delta", {"sessionId": "s", "turnId": "t", "text": "Hi "}),
        ("result/final", {"sessionId": "s", "turnId": "t", "text": "Hi there."}),
        ("thinking/delta", {"sessionId": "s", "turnId": "t", "text": "reasoning..."}),
        ("thinking/final", {"sessionId": "s", "turnId": "t", "text": "ok done"}),
        (
            "tool/started",
            {
                "sessionId": "s",
                "turnId": "t",
                "toolCallId": "call-1",
                "name": "read_file",
                "args": {"path": "/etc/hosts"},
            },
        ),
        (
            "tool/completed",
            {
                "sessionId": "s",
                "turnId": "t",
                "toolCallId": "call-1",
                "name": "read_file",
                "result": "127.0.0.1 localhost\n",
                "durationMs": 23,
            },
        ),
        (
            "approval/request",
            {
                "sessionId": "s",
                "turnId": "t",
                "approvalId": "ap-1",
                "kind": "tool_call",
                "payload": {"tool": "bash"},
                "timeoutMs": 30000,
            },
        ),
        (
            "approval/timeout",
            {"sessionId": "s", "turnId": "t", "approvalId": "ap-1", "kind": "tool_call"},
        ),
        (
            "usage",
            {
                "sessionId": "s",
                "turnId": "t",
                "inputTokens": 100,
                "outputTokens": 50,
                "cost": "0.0023",
                "model": "claude-3",
                "provider": "anthropic",
            },
        ),
    ],
)
def test_canonical_wire_notification_surfaces_verbatim_to_on_json(method: str, params: dict[str, Any]) -> None:
    payload = json.dumps({"method": method, "params": params}) + "\n"
    json_lines, non_json_lines = _drain(payload)
    assert non_json_lines == []
    assert len(json_lines) == 1
    assert json_lines[0]["method"] == method
    assert json_lines[0]["params"] == params


# ---------------------------------------------------------------------------
# Canonical turn sequence — full progression
# ---------------------------------------------------------------------------


def test_canonical_turn_sequence_preserved_in_order() -> None:
    """A representative successful turn emits the events in stable order."""
    events: list[dict[str, Any]] = [
        {"method": "progress", "params": {"sessionId": "s", "turnId": "t", "message": "starting"}},
        {
            "method": "tool/started",
            "params": {
                "sessionId": "s",
                "turnId": "t",
                "toolCallId": "c-1",
                "name": "read_file",
                "args": {},
            },
        },
        {
            "method": "tool/completed",
            "params": {
                "sessionId": "s",
                "turnId": "t",
                "toolCallId": "c-1",
                "name": "read_file",
                "result": "ok",
                "durationMs": 12,
            },
        },
        {"method": "result/delta", "params": {"sessionId": "s", "turnId": "t", "text": "Hello "}},
        {"method": "result/delta", "params": {"sessionId": "s", "turnId": "t", "text": "world."}},
        {"method": "result/final", "params": {"sessionId": "s", "turnId": "t", "text": "Hello world."}},
    ]
    payload = "".join(json.dumps(e) + "\n" for e in events)
    json_lines, non_json_lines = _drain(payload)
    assert non_json_lines == []
    assert [e["method"] for e in json_lines] == [e["method"] for e in events]


# ---------------------------------------------------------------------------
# Framing edge cases
# ---------------------------------------------------------------------------


def test_blank_lines_are_silently_skipped_not_treated_as_non_json() -> None:
    payload = "\n\n" + json.dumps({"method": "progress", "params": {}}) + "\n\n"
    json_lines, non_json_lines = _drain(payload)
    assert non_json_lines == []
    assert len(json_lines) == 1


def test_non_json_lines_routed_to_on_non_json() -> None:
    payload = (
        "ordinary log line\n" + json.dumps({"method": "progress", "params": {"x": 1}}) + "\n" + "another bare line\n"
    )
    json_lines, non_json_lines = _drain(payload)
    assert len(json_lines) == 1
    assert non_json_lines == ["ordinary log line", "another bare line"]


def test_json_parseable_but_not_object_routed_to_on_non_json() -> None:
    """Bare scalars are valid JSON but not wire envelopes — surface to on_non_json."""
    payload = "42\n" + json.dumps("a bare string") + "\n" + json.dumps({"method": "x"}) + "\n"
    json_lines, non_json_lines = _drain(payload)
    assert len(json_lines) == 1  # only the dict-valued line
    assert len(non_json_lines) == 2  # the 42 and the bare string


def test_on_non_json_optional_silently_drops_bad_lines() -> None:
    payload = "garbage\n" + json.dumps({"method": "progress", "params": {}}) + "\n"
    json_lines: list[dict[str, Any]] = []

    async def go() -> None:
        await parse_ndjson_stream(
            _stream_from_bytes(payload.encode("utf-8")),
            on_json=json_lines.append,
            # No on_non_json passed — bad line silently dropped.
        )

    asyncio.run(go())
    assert len(json_lines) == 1


def test_crlf_line_endings_handled_correctly() -> None:
    payload = json.dumps({"method": "progress", "params": {}}) + "\r\n"
    json_lines, non_json_lines = _drain(payload)
    assert non_json_lines == []
    assert len(json_lines) == 1


def test_payload_with_no_trailing_newline_still_yields_last_line() -> None:
    payload = json.dumps({"method": "progress", "params": {}})  # no trailing \n
    json_lines, _ = _drain(payload)
    assert len(json_lines) == 1


def test_empty_payload_completes_with_no_events() -> None:
    json_lines, non_json_lines = _drain("")
    assert json_lines == []
    assert non_json_lines == []
