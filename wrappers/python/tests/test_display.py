"""Tests for display adapter: onEvent push callback + subagent event filtering.

RED: fails because wrappers/python/src/amplifier_agent_client/display.py does not exist yet.
GREEN: passes once apply_display_filter is implemented.

Three filter cases:
(a) keeps everything when subagent_events='all' (including events with parent_turn_id)
(b) drops events with parent_turn_id when subagent_events='none'
(c) defaults to 'all' when subagent_events unset

Integration cases:
(d) push callback (on_event) receives kept events when wired into SessionHandle
(e) filter suppresses parent_turn_id events from both iterator and push callback when subagent_events='none'
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from amplifier_agent_client.display import apply_display_filter
from amplifier_agent_client.session import DisplayEvent, SessionHandle

# ---------------------------------------------------------------------------
# Unit tests: apply_display_filter
# ---------------------------------------------------------------------------


def _make_event(parent_turn_id: str | None = None) -> DisplayEvent:
    return DisplayEvent(
        type="result/delta",
        session_id="s1",
        turn_id="t1",
        payload={"parentTurnId": parent_turn_id} if parent_turn_id else {},
        parent_turn_id=parent_turn_id,
    )


def test_apply_display_filter_all_keeps_events_with_parent_turn_id() -> None:
    """(a) subagent_events='all': keeps events with and without parent_turn_id."""
    keep = apply_display_filter(subagent_events="all")
    assert keep(_make_event(parent_turn_id="parent-1")) is True
    assert keep(_make_event(parent_turn_id=None)) is True


def test_apply_display_filter_none_drops_events_with_parent_turn_id() -> None:
    """(b) subagent_events='none': drops events with parent_turn_id, keeps those without."""
    keep = apply_display_filter(subagent_events="none")
    assert keep(_make_event(parent_turn_id="parent-1")) is False
    assert keep(_make_event(parent_turn_id=None)) is True


def test_apply_display_filter_default_is_all() -> None:
    """(c) subagent_events unset: defaults to 'all' (keeps everything)."""
    keep = apply_display_filter()
    assert keep(_make_event(parent_turn_id="parent-1")) is True
    assert keep(_make_event(parent_turn_id=None)) is True


# ---------------------------------------------------------------------------
# Integration tests: on_event push callback + filtering wired into SessionHandle
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


@pytest.mark.asyncio
async def test_on_event_push_callback_receives_kept_events() -> None:
    """(d) on_event push callback receives the same events as the iterator."""
    rpc = StubRpc()
    pushed: list[DisplayEvent] = []

    async def terminate() -> None:
        pass

    handle = SessionHandle(
        rpc=rpc,
        session_id="sess-d",
        terminate=terminate,
        display_on_event=lambda ev: pushed.append(ev),
    )
    stream = handle.submit("hello")
    pulled: list[DisplayEvent] = []

    async def consume() -> None:
        async for evt in stream:
            pulled.append(evt)

    consuming = asyncio.create_task(consume())
    await asyncio.sleep(0)

    rpc.notify("result/delta", {"sessionId": "sess-d", "turnId": "t1", "text": "hi"})
    rpc.notify("result/final", {"sessionId": "sess-d", "turnId": "t1", "text": "hi"})
    rpc.resolve_call("turn/submit", {"reply": "hi", "turnId": "t1", "sessionId": "sess-d"})

    await consuming

    assert [e.type for e in pulled] == ["result/delta", "result/final"]
    assert [e.type for e in pushed] == ["result/delta", "result/final"]


@pytest.mark.asyncio
async def test_subagent_none_suppresses_parent_turn_id_events() -> None:
    """(e) subagent_events='none' suppresses parentTurnId events from both iterator and callback."""
    rpc = StubRpc()
    pushed: list[DisplayEvent] = []

    async def terminate() -> None:
        pass

    handle = SessionHandle(
        rpc=rpc,
        session_id="sess-e",
        terminate=terminate,
        display_on_event=lambda ev: pushed.append(ev),
        display_subagent_events="none",
    )
    stream = handle.submit("hello")
    pulled: list[DisplayEvent] = []

    async def consume() -> None:
        async for evt in stream:
            pulled.append(evt)

    consuming = asyncio.create_task(consume())
    await asyncio.sleep(0)

    # sub-agent progress event (should be filtered out)
    rpc.notify(
        "result/delta",
        {
            "sessionId": "sess-e",
            "turnId": "t2",
            "parentTurnId": "parent-t1",
            "text": "sub-agent chunk",
        },
    )
    # normal event (should pass through)
    rpc.notify("result/delta", {"sessionId": "sess-e", "turnId": "t1", "text": "normal"})
    rpc.notify("result/final", {"sessionId": "sess-e", "turnId": "t1", "text": "done"})
    rpc.resolve_call("turn/submit", {"reply": "done", "turnId": "t1", "sessionId": "sess-e"})

    await consuming

    # parentTurnId event should be suppressed from both paths
    assert [e.type for e in pulled] == ["result/delta", "result/final"]
    assert [e.type for e in pushed] == ["result/delta", "result/final"]
