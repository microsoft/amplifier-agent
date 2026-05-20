"""Tests for L14 wire-level safety-net: synthesize_result_final_if_needed.

These tests verify the Appendix B safety-net contract:
- When engine omits result/final, the helper synthesizes it on the wire.
- No double-emission when engine already emitted result/final.
- No synthesis when reply text is empty or None.
"""

from __future__ import annotations

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


def _parse_notifications(writer: _FakeWriter) -> list[dict]:
    """Parse all written bytes as JSON-RPC notifications."""
    result = []
    for raw in writer.written:
        msg = json.loads(raw.rstrip(b"\n"))
        result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizes_when_engine_omitted_result_final() -> None:
    """When engine did not emit result/final, helper synthesizes it with synthesized:true marker."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioDisplaySystem,
        synthesize_result_final_if_needed,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)

    # Precondition: result/final was NOT emitted by the engine
    assert display.result_final_emitted is False

    reply = {"reply": "Hello, world!", "turnId": "turn-42"}
    result = await synthesize_result_final_if_needed(display, reply=reply)

    assert result is True, "Should return True when synthesis was performed"

    # One notification should have been written
    notifications = _parse_notifications(writer)
    assert len(notifications) == 1

    notif = notifications[0]
    assert notif["method"] == "result/final"
    params = notif["params"]
    assert params["text"] == "Hello, world!"
    assert params["synthesized"] is True, "Must carry synthesized:true debug marker (Appendix B)"
    assert params["turnId"] == "turn-42"


@pytest.mark.asyncio
async def test_does_not_synthesize_when_engine_already_emitted() -> None:
    """No double-emission: when engine already emitted result/final, helper returns False."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioDisplaySystem,
        synthesize_result_final_if_needed,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)

    # Simulate engine emitting result/final naturally
    await display.emit("result/final", {"text": "Engine response"})
    assert display.result_final_emitted is True

    # Clear the writer to track only what the helper might emit
    writer.written.clear()

    reply = {"reply": "Hello, world!"}
    result = await synthesize_result_final_if_needed(display, reply=reply)

    assert result is False, "Should return False when result/final was already emitted"
    assert len(writer.written) == 0, "No additional notification should be written"


@pytest.mark.asyncio
async def test_does_not_synthesize_when_reply_text_empty() -> None:
    """No synthesis when reply text is an empty string."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioDisplaySystem,
        synthesize_result_final_if_needed,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)

    assert display.result_final_emitted is False

    reply = {"reply": ""}
    result = await synthesize_result_final_if_needed(display, reply=reply)

    assert result is False, "Should return False when reply text is empty string"
    assert len(writer.written) == 0, "No notification should be written for empty text"


@pytest.mark.asyncio
async def test_does_not_synthesize_when_reply_text_none() -> None:
    """No synthesis when reply key is absent (None from get)."""
    from amplifier_agent_lib.protocol_points.defaults_stdio import (
        StdioDisplaySystem,
        synthesize_result_final_if_needed,
    )

    writer = _FakeWriter()
    display = StdioDisplaySystem(writer)

    assert display.result_final_emitted is False

    # reply dict has no 'reply' key — text will be None
    reply = {"turnId": "turn-99"}
    result = await synthesize_result_final_if_needed(display, reply=reply)

    assert result is False, "Should return False when reply text is None"
    assert len(writer.written) == 0, "No notification should be written for None text"
