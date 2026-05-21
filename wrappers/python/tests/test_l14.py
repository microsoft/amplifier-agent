"""Tests for L14 client-side result/final synthesis (design §4.6 contract #1).

Pure function tests:
(a) saw_final=True  → returns None (no synthesis needed)
(b) reply=None      → returns None (nothing to synthesize from)
(c) saw_final=False, reply='hello' → returns dict with
    type='result/final', synthesized=True, payload['text']='hello'

Integration test:
(d) Branch B — engine omits result/final but provides reply in turn/submit
    response → last yielded DisplayEvent is synthesized result/final
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from amplifier_agent_client.l14 import synthesize_final_if_missing
from amplifier_agent_client.session import DisplayEvent, SessionHandle

# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


def test_returns_none_when_saw_final() -> None:
    """(a) saw_final=True → returns None."""
    result = synthesize_final_if_missing(
        saw_final=True,
        reply="hello",
        session_id="sess-1",
        turn_id="turn-1",
    )
    assert result is None


def test_returns_none_when_reply_is_none() -> None:
    """(b) reply=None → returns None."""
    result = synthesize_final_if_missing(
        saw_final=False,
        reply=None,
        session_id="sess-1",
        turn_id="turn-1",
    )
    assert result is None


def test_returns_synthesized_event_dict() -> None:
    """(c) saw_final=False, reply='hello' → returns synthesized dict."""
    result = synthesize_final_if_missing(
        saw_final=False,
        reply="hello",
        session_id="sess-1",
        turn_id="turn-1",
    )
    assert result is not None
    assert result["type"] == "result/final"
    assert result["synthesized"] is True
    assert result["payload"]["text"] == "hello"


# ---------------------------------------------------------------------------
# Integration test helpers (re-use StubRpc pattern from test_session.py)
# ---------------------------------------------------------------------------


class StubRpc:
    """Minimal stub RPC for testing."""

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


# ---------------------------------------------------------------------------
# Integration test — Branch B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_b_synthesizes_final_when_engine_omits_it() -> None:
    """(d) Branch B: synthesizes result/final when engine omits it but provides reply."""
    rpc = StubRpc()

    async def terminate() -> None:
        pass

    handle = SessionHandle(rpc=rpc, session_id="sess-l14", terminate=terminate)
    stream = handle.submit("hello")
    events: list[DisplayEvent] = []

    async def consume() -> None:
        async for evt in stream:
            events.append(evt)

    consuming = asyncio.create_task(consume())

    # Two sleep(0) calls are needed:
    # - First: lets consume() start, register on_notif, and create submit_task (schedules it)
    # - Second: lets submit_task run its first step and register its pending RPC future
    # Without the second sleep, resolve_call would be a no-op (future not yet created).
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Drive a delta event but NO result/final (Branch B scenario)
    rpc.notify("result/delta", {"sessionId": "sess-l14", "turnId": "turn-l14", "text": "Hello"})

    # Resolve turn/submit with a reply — engine omits result/final
    rpc.resolve_call("turn/submit", {"reply": "Hello", "turnId": "turn-l14", "sessionId": "sess-l14"})

    await consuming

    assert len(events) >= 2
    last = events[-1]
    assert last.type == "result/final"
    assert last.synthesized is True
