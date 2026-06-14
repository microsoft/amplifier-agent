"""Regression test for sync iterator early-break teardown.

The sync wrapper wraps an async generator from ``SessionHandle.submit()``.
When a sync caller breaks out of the iterator early (e.g. on a ``result``
event), the async generator must be explicitly ``aclose()``'d so that:

- Background tasks (activity ticker, NDJSON parser) are cancelled.
- The MCP spill file is unlinked.
- Python does not emit ``RuntimeError: async generator ignored GeneratorExit``.

We do not exercise a real subprocess here — we substitute a tiny stub for
``SessionHandle`` that records whether its async generator's ``finally``
ran.  This is enough to prove the sync wrapper drives ``aclose()`` on early
break.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from amplifier_agent_py import (
    ActivityEvent,
    DisplayEvent,
    EngineInfo,
    InitEvent,
    ResultEvent,
    SyncSessionHandle,
)


class _StubSessionHandle:
    """Minimal stand-in for ``SessionHandle`` that tracks teardown."""

    def __init__(self) -> None:
        self.cleanup_ran = False
        self.cancelled = False

    def get_engine_info(self) -> EngineInfo:
        return EngineInfo(
            binary_path="/stub",
            protocol_version="0.3.0",
            engine_version="0.0.0",
            bundle_digest="",
        )

    def submit(self, prompt: str) -> AsyncIterator[DisplayEvent]:
        # ``prompt`` is unused; we want a fixed sequence to drive the assertions.
        del prompt
        return self._make_iter()

    async def _make_iter(self) -> AsyncIterator[DisplayEvent]:
        try:
            yield InitEvent(session_id="stub-1")
            yield ResultEvent(text="hello")
            # If the caller forgets to aclose() us, this activity event will
            # be yielded later and the for-loop will keep running — proving
            # the teardown path is broken.
            yield ActivityEvent()
        finally:
            self.cleanup_ran = True

    async def cancel(self) -> None:
        self.cancelled = True

    async def dispose(self) -> None:
        await self.cancel()


def test_sync_iterator_runs_finally_on_early_break() -> None:
    """Breaking on ``result`` must aclose() the underlying async generator."""
    loop = asyncio.new_event_loop()
    stub = _StubSessionHandle()
    handle = SyncSessionHandle(stub, loop)  # type: ignore[arg-type]

    try:
        events_seen: list[DisplayEvent] = []
        for event in handle.submit("any"):
            events_seen.append(event)
            if event.type == "result":
                break

        # Sanity: we received init then result, then broke.
        assert [e.type for e in events_seen] == ["init", "result"]
        # The underlying async generator's finally block must have run on
        # early break — proving sync.py drives aclose() correctly.
        assert stub.cleanup_ran is True
    finally:
        handle.close()


def test_sync_iterator_runs_finally_on_exhaustion() -> None:
    """Even when the iterator is fully drained, finally still runs."""
    loop = asyncio.new_event_loop()
    stub = _StubSessionHandle()
    handle = SyncSessionHandle(stub, loop)  # type: ignore[arg-type]

    try:
        events_seen: list[DisplayEvent] = []
        events_seen.extend(handle.submit("any"))

        assert [e.type for e in events_seen] == ["init", "result", "activity"]
        assert stub.cleanup_ran is True
    finally:
        handle.close()


def test_sync_iterator_runs_finally_on_exception() -> None:
    """If the consumer raises inside the for-loop, cleanup still runs."""
    loop = asyncio.new_event_loop()
    stub = _StubSessionHandle()
    handle = SyncSessionHandle(stub, loop)  # type: ignore[arg-type]

    class _ConsumerExploded(Exception):
        pass

    try:
        try:
            for event in handle.submit("any"):
                if event.type == "result":
                    raise _ConsumerExploded("boom")
        except _ConsumerExploded:
            pass
        assert stub.cleanup_ran is True
    finally:
        handle.close()


def test_sync_handle_context_manager_closes_loop() -> None:
    """``with`` block must close the owned event loop on exit."""
    loop = asyncio.new_event_loop()
    stub = _StubSessionHandle()

    with SyncSessionHandle(stub, loop) as handle:  # type: ignore[arg-type]
        assert handle.get_engine_info().protocol_version == "0.3.0"

    assert loop.is_closed() is True
    # dispose() ran on context-manager exit via close() — sanity check.
    assert stub.cancelled is True


def _unused_to_keep_typing_happy(_: Any) -> None:
    """Suppress unused-import noise from the typing-only ``Any`` import."""
