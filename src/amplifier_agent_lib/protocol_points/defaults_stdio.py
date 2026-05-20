"""Stdio Mode B defaults — JSON-RPC display bridge.

Per design §6 Mode B defaults:
- display writes JSON-RPC notifications to a stdout writer owned by stdio_loop;
- tracks ``result/final`` emission for the L14 safety-net in stdio_loop (Task 5).

CRITICAL: this module must NOT default to sys.stdout.
The writer is injected by the caller (stdio_loop).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from amplifier_agent_lib import jsonrpc


@runtime_checkable
class _Writer(Protocol):
    """Minimal async writer interface for JSON-RPC framing."""

    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...


class StdioDisplaySystem:
    """Mode B display bridge — translates engine emit() calls to JSON-RPC notifications.

    The writer MUST be provided by the caller (typically the stdio_loop's stdout
    writer).  This class never touches ``sys.stdout`` directly.

    Tracks whether ``result/final`` was emitted in the current turn; this flag
    is consulted by the L14 safety-net in stdio_loop (Task 5).
    """

    def __init__(self, writer: _Writer) -> None:
        self._writer = writer
        self._result_final_emitted: bool = False

    @property
    def result_final_emitted(self) -> bool:
        """True if ``result/final`` was emitted in the current turn."""
        return self._result_final_emitted

    def reset_for_turn(self) -> None:
        """Clear the ``result_final_emitted`` flag for a new turn."""
        self._result_final_emitted = False

    async def emit(self, event_type: str, payload: dict) -> None:  # type: ignore[type-arg]
        """Translate a display event into a JSON-RPC notification on the writer.

        Builds a notification via :func:`jsonrpc.make_notification` and writes
        it via :func:`jsonrpc.write_message`.  If *event_type* is
        ``'result/final'``, sets :attr:`result_final_emitted` to ``True``.
        """
        notification = jsonrpc.make_notification(method=event_type, params=payload)
        await jsonrpc.write_message(self._writer, notification)
        if event_type == "result/final":
            self._result_final_emitted = True


async def synthesize_result_final_if_needed(
    display: StdioDisplaySystem,
    *,
    reply: dict,  # type: ignore[type-arg]
) -> bool:
    """Wire-level safety-net for the L14 contract (Appendix B).

    After ``Engine.dispatch()`` returns, ``stdio_loop`` calls this helper to
    guarantee that a ``result/final`` notification precedes the outbound
    ``turn/submit`` response on the wire.

    Returns ``True`` if a synthetic ``result/final`` was emitted, ``False``
    otherwise (i.e. either the engine already emitted it, or the reply carries
    no text).

    The synthesized notification carries a ``synthesized: true`` debug marker
    so that observability tooling can distinguish synthesized emissions from
    natural engine emissions.

    Args:
        display: The :class:`StdioDisplaySystem` for the current turn.
        reply:   The outbound ``turn/submit`` response dict (wire format).
    """
    # Guard 1: engine already emitted result/final — no double-emission.
    if display.result_final_emitted:
        return False

    # Guard 2: only synthesize when reply carries non-empty text.
    text = reply.get("reply")
    if not isinstance(text, str) or text == "":
        return False

    # Build the synthesized payload per Appendix B.
    payload: dict = {"text": text, "synthesized": True}
    turn_id = reply.get("turnId")
    if turn_id is not None:
        payload["turnId"] = turn_id

    await display.emit("result/final", payload)
    return True


class StdioApprovalSystem:
    """Mode B approval bridge — bidirectional approval with mandatory timeout.

    Translates engine ``request()`` calls into JSON-RPC ``approval/request``
    requests on stdout and waits for the host to respond via
    :meth:`handle_response`.

    Timeouts are mandatory: if no response arrives within *timeout_ms*
    milliseconds the system emits an ``approval/timeout`` notification (via the
    injected :class:`StdioDisplaySystem`) and returns a cancel action.
    """

    def __init__(
        self,
        writer: _Writer,
        *,
        display: StdioDisplaySystem,
        id_seed: int = 1,
    ) -> None:
        self._writer = writer
        self._display = display
        self._id_seed = id_seed
        self._pending: dict[int, asyncio.Future[Any]] = {}

    def _allocate_id(self) -> int:
        """Return the next request id and advance the seed."""
        req_id = self._id_seed
        self._id_seed += 1
        return req_id

    async def request(
        self,
        *,
        kind: str,
        payload: dict,  # type: ignore[type-arg]
        timeout_ms: int,
    ) -> dict:  # type: ignore[type-arg]
        """Send an approval/request and await the host's response.

        Writes the request to stdout, registers a pending Future, and awaits
        resolution within *timeout_ms* milliseconds.  On timeout, emits an
        ``approval/timeout`` notification and returns a cancel/timeout action.

        Args:
            kind:       Approval kind string (e.g. ``'tool/call'``).
            payload:    Arbitrary JSON-serialisable approval payload.
            timeout_ms: Maximum wait in milliseconds before auto-cancellation.

        Returns:
            A dict with at least an ``'action'`` key (e.g. ``'accept'``,
            ``'decline'``, or ``'cancel'``).
        """
        req_id = self._allocate_id()
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "approval/request",
            "params": {
                "kind": kind,
                "payload": payload,
                "timeoutMs": timeout_ms,
            },
        }
        await jsonrpc.write_message(self._writer, message)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000.0)  # type: ignore[return-value]
        except TimeoutError:
            await self._display.emit("approval/timeout", {"requestId": req_id, "kind": kind})
            return {"action": "cancel", "reason": "timeout"}
        finally:
            self._pending.pop(req_id, None)

    def handle_response(self, message: dict) -> None:  # type: ignore[type-arg]
        """Route an incoming JSON-RPC response to the awaiting Future.

        Called by ``stdio_loop`` for every incoming response message.  Unknown
        request ids are silently ignored (defensive).

        Args:
            message: A JSON-RPC response dict (must have ``'id'`` and either
                     ``'result'`` or ``'error'``).
        """
        msg_id = message.get("id")
        future = self._pending.get(msg_id)  # type: ignore[arg-type]
        if future is None or future.done():
            # Unknown id or already resolved/cancelled — ignore silently.
            return

        if "error" in message:
            future.set_result({"action": "cancel", "reason": "error", "error": message["error"]})
        elif "result" in message:
            result = message["result"]
            if not isinstance(result, dict):
                future.set_result({"action": "cancel", "reason": "malformed"})
            else:
                future.set_result(result)
        else:
            future.set_result({"action": "cancel", "reason": "malformed"})
