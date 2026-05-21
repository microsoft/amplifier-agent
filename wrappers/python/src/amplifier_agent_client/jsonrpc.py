"""JSON-RPC 2.0 client with per-request-id correlation and notification fanout.

Layers JSON-RPC 2.0 semantics on top of any _TransportLike (send/on_frame).

Design:
- call(): allocates a unique request id, creates an asyncio.Future, sends the request frame.
- _dispatch(): routes incoming frames:
  - response (has 'id', no 'method') → resolve/reject matching pending Future
  - server-initiated request (has 'id' AND 'method') → call registered handler, send result
  - notification (has 'method', no 'id') → fanout to all on_notification subscribers
- NC-L16 is designed out: each call() has its own independent Future row in _pending,
  so two concurrent calls can never interfere.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class _TransportLike(Protocol):
    """Minimal transport interface: send a frame and register a frame callback."""

    def send(self, obj: Any) -> None: ...

    def on_frame(self, cb: Callable[[Any], None]) -> None: ...


# Handler type: async function accepting params, returning the result
RequestHandler = Callable[[Any], Coroutine[Any, Any, Any]]


class JsonRpcClient:
    """JSON-RPC 2.0 client layered on top of any _TransportLike transport."""

    def __init__(self, transport: _TransportLike) -> None:
        self._transport = transport
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notif_subs: list[Callable[[dict[str, Any]], None]] = []
        self._request_handlers: dict[str, RequestHandler] = {}
        # Keep strong references to background request-handling tasks (RUF006).
        self._tasks: set[asyncio.Task[None]] = set()

        transport.on_frame(self._dispatch)

    async def call(self, method: str, params: Any = None) -> Any:
        """Send a JSON-RPC 2.0 request and await the result.

        Allocates a unique id per call — two concurrent calls have independent Futures.
        """
        req_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = future

        self._transport.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        return await future

    def on_notification(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Register a subscriber for server-initiated notifications.

        All subscribers are called for every notification.
        """
        self._notif_subs.append(cb)

    def on_request(self, method: str, handler: RequestHandler) -> None:
        """Register a handler for a specific server-initiated request method.

        The handler is awaited and its return value is sent back as the result.
        Unregistered methods receive a -32601 (Method not found) error response.
        """
        self._request_handlers[method] = handler

    def _dispatch(self, frame: Any) -> None:
        """Route an incoming frame to the appropriate handler."""
        if not isinstance(frame, dict):
            return

        has_id = "id" in frame
        has_method = "method" in frame

        if has_id and not has_method:
            # Response to a client call (result or error)
            self._handle_response(frame)
        elif has_id and has_method:
            # Server-initiated request — dispatch to registered handler.
            # Keep a strong reference so the task is not garbage-collected (RUF006).
            task = asyncio.create_task(self._handle_request(frame))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif has_method and not has_id:
            # Notification — fanout to subscribers
            self._handle_notification(frame)
        # Unknown frame shapes are silently dropped

    def _handle_response(self, frame: dict[str, Any]) -> None:
        req_id = frame["id"]
        future = self._pending.pop(req_id, None)
        if future is None or future.done():
            return

        if "error" in frame:
            future.set_exception(Exception(str(frame["error"])))
        else:
            future.set_result(frame.get("result"))

    async def _handle_request(self, frame: dict[str, Any]) -> None:
        req_id = frame["id"]
        method = frame["method"]
        params = frame.get("params")

        handler = self._request_handlers.get(method)
        if handler is None:
            self._transport.send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
            return

        try:
            result = await handler(params)
            self._transport.send({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:
            self._transport.send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": str(exc)},
                }
            )

    def _handle_notification(self, frame: dict[str, Any]) -> None:
        notif = {"method": frame["method"], "params": frame.get("params")}
        for sub in self._notif_subs:
            sub(notif)
