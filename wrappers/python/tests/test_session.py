"""Tests for SessionHandle.submit() returning AsyncIterator[DisplayEvent].

RED: fails because wrappers/python/src/amplifier_agent_client/session.py
     does not exist yet.
GREEN: passes once SessionHandle is implemented.

TDD bullets:
(a) yields display events then ends when result/final arrives —
    drive 2 result/delta notifs + result/final; collected event types
    should equal ['result/delta','result/delta','result/final']
(b) second submit() throws — one-shot per session (D10),
    matches /one-shot|already submitted/i
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from amplifier_agent_client.session import DisplayEvent, SessionHandle


class StubRpc:
    """Minimal stub RPC for testing: captures sent calls, exposes methods
    to simulate incoming notifications and resolve pending calls."""

    def __init__(self) -> None:
        self._notif_cbs: list[Callable[[dict[str, Any]], None]] = []
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._call_count = 0

    async def call(self, method: str, params: Any = None) -> Any:
        loop = asyncio.get_running_loop()
        key = f"{method}:{self._call_count}"
        self._call_count += 1
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[key] = fut
        return await fut

    def on_notification(self, cb: Callable[[dict[str, Any]], None]) -> None:
        self._notif_cbs.append(cb)

    def notify(self, method: str, params: Any = None) -> None:
        """Simulate an incoming notification from the server."""
        for cb in self._notif_cbs:
            cb({"method": method, "params": params})

    def resolve_call(self, method: str, result: Any = None) -> None:
        """Resolve the first pending call for the given method."""
        for key, fut in list(self._pending.items()):
            if key.startswith(method + ":") and not fut.done():
                del self._pending[key]
                fut.set_result(result)
                return


@pytest.mark.asyncio
async def test_submit_yields_display_events_and_ends_on_result_final() -> None:
    """(a) yields display events then ends when result/final arrives."""
    rpc = StubRpc()

    async def terminate() -> None:
        pass

    handle = SessionHandle(rpc=rpc, session_id="sess-1", terminate=terminate)
    stream = handle.submit("hello")
    events: list[DisplayEvent] = []

    async def consume() -> None:
        async for evt in stream:
            events.append(evt)

    consuming = asyncio.create_task(consume())

    # Give generator one tick to start and register notification callback
    await asyncio.sleep(0)

    # Drive 2 result/delta notifications then result/final
    rpc.notify("result/delta", {"sessionId": "sess-1", "turnId": "turn-1", "text": "Hello"})
    rpc.notify("result/delta", {"sessionId": "sess-1", "turnId": "turn-1", "text": " World"})
    rpc.notify("result/final", {"sessionId": "sess-1", "turnId": "turn-1", "text": "Hello World"})

    # Resolve turn/submit RPC response (comes after result/final in normal flow)
    rpc.resolve_call("turn/submit", {"reply": "Hello World", "turnId": "turn-1", "sessionId": "sess-1"})

    await consuming

    types = [e.type for e in events]
    assert types == ["result/delta", "result/delta", "result/final"]


def test_second_submit_raises_runtime_error() -> None:
    """(b) second submit() throws — one-shot per session (D10), matches /one-shot|already submitted/i."""
    rpc = StubRpc()
    handle = SessionHandle(rpc=rpc, session_id="sess-2", terminate=lambda: None)

    # First submit is fine
    handle.submit("first")

    # Second submit should raise RuntimeError matching the pattern
    with pytest.raises(RuntimeError, match=r"one-shot|already submitted"):
        handle.submit("second")
