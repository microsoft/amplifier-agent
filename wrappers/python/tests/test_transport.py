"""Tests for Transport: subprocess spawn + NDJSON framing.

RED: fails because wrappers/python/src/amplifier_agent_client/transport.py
     does not exist yet.
GREEN: passes once Transport is implemented.

TDD bullets:
- (a) cat echo: send JSON frame, receive it back via frames() async iterator
- (b) non-JSON dropped: only valid JSON lines are yielded by frames()
- (c) terminate: kills sleep 60, returns non-zero exit code
"""

from __future__ import annotations

import pytest

from amplifier_agent_client.transport import Transport


@pytest.mark.asyncio
async def test_cat_echo_roundtrip() -> None:
    """Transport round-trip: cat echoes back the JSON frame we sent."""
    t = Transport(command="cat", args=[], env={})
    await t.start()
    await t.send({"hello": "world"})

    received: list[object] = []
    async for frame in t.frames():
        received.append(frame)
        break  # Got the echo; stop iterating

    await t.terminate()
    assert received == [{"hello": "world"}]


@pytest.mark.asyncio
async def test_non_json_lines_dropped() -> None:
    """Non-JSON stdout lines are dropped silently; only valid JSON is yielded."""
    t = Transport(
        command="sh",
        args=["-c", r'printf "not json\n{\"ok\":true}\n"'],
        env={},
    )
    await t.start()

    received: list[object] = []
    async for frame in t.frames():
        received.append(frame)
    # Process exits on its own; frames() exits when stdout is exhausted

    assert received == [{"ok": True}]


@pytest.mark.asyncio
async def test_terminate_kills_process() -> None:
    """terminate() sends SIGTERM and waits; returns non-zero exit code."""
    t = Transport(command="sh", args=["-c", "sleep 60"], env={})
    await t.start()

    code = await t.terminate()

    # On Unix, SIGTERM gives returncode -15 (negative signal number)
    assert code != 0
