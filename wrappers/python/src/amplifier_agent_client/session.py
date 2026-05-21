"""SessionHandle — one-shot session wrapper.

submit(prompt) returns AsyncIterator[DisplayEvent]:
  - Sends turn/submit via JSON-RPC
  - Yields every display/event-shaped notification that arrives
  - Terminates the iterator when result/final notification is observed
    OR when the turn/submit JSON-RPC response arrives (whichever first)

Per design D10, only one submit() per subprocess lifetime in v1.
A second call raises RuntimeError.

cancel()/dispose() both call terminate() on the underlying transport (D3).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable
from typing import Any


class AaaError(Exception):
    """Typed error for AaA wrapper lifecycle and protocol violations."""

    def __init__(self, code: str, remediation: str | None = None) -> None:
        super().__init__(remediation or code)
        self.code = code
        self.remediation = remediation


class DisplayEvent:
    """A display event yielded by SessionHandle.submit().

    Attributes:
        type:           Notification method name, e.g. 'result/delta'.
        session_id:     Session identifier.
        turn_id:        Turn identifier.
        parent_turn_id: Present on sub-agent events.
        synthesized:    True if wrapper-synthesized via L14 path.
        payload:        The full notification params dict.
    """

    def __init__(
        self,
        type: str,  # shadows built-in; intentional — mirrors the wire field name
        session_id: str,
        turn_id: str,
        payload: dict[str, Any],
        parent_turn_id: str | None = None,
        synthesized: bool | None = None,
    ) -> None:
        self.type = type
        self.session_id = session_id
        self.turn_id = turn_id
        self.payload = payload
        self.parent_turn_id = parent_turn_id
        self.synthesized = synthesized

    def __repr__(self) -> str:
        return f"DisplayEvent(type={self.type!r}, turn_id={self.turn_id!r})"


#: The notification method that signals end-of-turn.
TERMINAL_NOTIFICATION = "result/final"


class SessionHandle:
    """One-shot session handle.

    Args:
        rpc:        JSON-RPC client with call() and on_notification().
        session_id: Session identifier for this handle.
        terminate:  Callable that SIGTERMs the subprocess (D3).
    """

    def __init__(
        self,
        rpc: Any,
        session_id: str,
        terminate: Callable[[], Any],
    ) -> None:
        self._rpc = rpc
        self._session_id = session_id
        self._terminate = terminate
        self._submitted = False

    def submit(self, prompt: str) -> AsyncIterator[DisplayEvent]:
        """Submit a prompt and return an AsyncIterator of DisplayEvents.

        One-shot per session (D10): raises RuntimeError on second call.
        """
        if self._submitted:
            raise RuntimeError("SessionHandle.submit() is one-shot per session (D10); already submitted")
        self._submitted = True

        rand = hex(random.randint(0, 0xFFFFFFFF))[2:]
        turn_id = f"turn-{int(time.time() * 1000)}-{rand}"

        return self._stream(turn_id, prompt)

    async def _stream(self, turn_id: str, prompt: str) -> AsyncIterator[DisplayEvent]:
        """Async generator that buffers and yields DisplayEvents.

        Uses asyncio.Queue with None sentinel for termination.
        - on_notif puts events; result/final also puts sentinel.
        - submit_task's finally puts sentinel when RPC call settles.
        """
        queue: asyncio.Queue[DisplayEvent | None] = asyncio.Queue()

        def on_notif(notif: dict[str, Any]) -> None:
            method = notif.get("method", "")
            params: dict[str, Any] = notif.get("params") or {}
            event = DisplayEvent(
                type=method,
                session_id=str(params.get("sessionId", self._session_id)),
                turn_id=str(params.get("turnId", turn_id)),
                payload=params,
                parent_turn_id=params.get("parentTurnId"),
            )
            queue.put_nowait(event)
            # result/final signals end-of-turn; put sentinel after the event.
            if method == TERMINAL_NOTIFICATION:
                queue.put_nowait(None)

        self._rpc.on_notification(on_notif)

        async def submit_task() -> None:
            try:
                await self._rpc.call(
                    "turn/submit",
                    {
                        "sessionId": self._session_id,
                        "turnId": turn_id,
                        "prompt": prompt,
                    },
                )
            finally:
                # Sentinel when response arrives (success or error).
                queue.put_nowait(None)

        # Keep a strong reference to prevent premature GC (RUF006).
        # The task runs in the background; the sentinel it puts in the queue
        # (via the finally block) terminates the iterator if result/final
        # has not already done so.
        task = asyncio.create_task(submit_task())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            # On normal exit the task is still running (awaiting the RPC
            # response); leave it to complete naturally in the background.
            # On cancellation it will be GC'd with the generator frame.
            _ = task  # hold reference until generator frame is collected

    async def cancel(self) -> None:
        """SIGTERM the subprocess (D3)."""
        await self._terminate()

    async def dispose(self) -> None:
        """Graceful shutdown; SIGTERM if needed (D3)."""
        await self._terminate()
