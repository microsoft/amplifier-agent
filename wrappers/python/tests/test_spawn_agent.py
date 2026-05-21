"""Tests for spawn_agent() public API + get_engine_info().

TDD bullets:
(a) spawn_agent() with FakeTransport returns SessionHandle whose
    get_engine_info() returns protocol_version='2026-05-aaa-v0' and
    binary_path='/dev/null'
(b) spawn_agent() raises AaaError(lifecycle_unsupported) when lifecycle
    is not 'one-shot' (D10)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from amplifier_agent_client import AaaError, SessionHandle, spawn_agent


class FakeTransport:
    """FakeTransport: implements the transport contract expected by spawn_agent().
    Auto-responds to agent/initialize with a valid result.
    """

    def __init__(self) -> None:
        self._frame_cbs: list[Callable[[Any], None]] = []

    def on_frame(self, cb: Callable[[Any], None]) -> None:
        self._frame_cbs.append(cb)

    def send(self, obj: Any) -> None:
        if isinstance(obj, dict) and obj.get("method") == "agent/initialize":
            req_id = obj.get("id")
            # Schedule response on next event-loop iteration so the
            # JsonRpcClient future can be awaited first.
            loop = asyncio.get_running_loop()
            loop.call_soon(self._dispatch_response, req_id)

    def _dispatch_response(self, req_id: Any) -> None:
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "capabilities": {},
                "serverInfo": {"name": "test-agent", "version": "0.0.0"},
                "sessionState": {"sessionId": "fake-session-id", "resumed": False},
            },
        }
        for cb in self._frame_cbs:
            cb(response)

    async def start(self) -> None:
        pass

    async def terminate(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_spawn_agent_returns_handle_with_engine_info() -> None:
    """(a) spawn_agent() returns SessionHandle with get_engine_info() returning
    protocol_version='2026-05-aaa-v0' and binary_path='/dev/null'.
    """
    handle = await spawn_agent(
        lifecycle="one-shot",
        session_id="test-session",
        _binary_resolver=lambda: "/dev/null",
        _version_probe=lambda *_args, **_kwargs: {
            "version": "1.2.3",
            "protocolVersion": "2026-05-aaa-v0",
            "bundleDigest": "deadbeef",
        },
        _transport_factory=lambda: FakeTransport(),
    )

    assert isinstance(handle, SessionHandle)
    info = handle.get_engine_info()
    assert info["protocol_version"] == "2026-05-aaa-v0"
    assert info["binary_path"] == "/dev/null"


@pytest.mark.asyncio
async def test_spawn_agent_raises_lifecycle_unsupported() -> None:
    """(b) spawn_agent() raises AaaError(lifecycle_unsupported) when lifecycle
    is not 'one-shot'.
    """
    with pytest.raises(AaaError) as exc_info:
        await spawn_agent(
            lifecycle="burst",  # type: ignore[arg-type]
            session_id="test",
        )
    assert exc_info.value.code == "lifecycle_unsupported"
