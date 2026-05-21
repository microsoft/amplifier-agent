"""Tests for JsonRpcClient: per-request-id correlation + notification fanout.

RED: fails because wrappers/python/src/amplifier_agent_client/jsonrpc.py
     does not exist yet.
GREEN: passes once JsonRpcClient is implemented.

TDD bullets:
(a) call() resolves when matching result arrives
(b) two concurrent calls do not interfere (different ids, can resolve in reverse order)
(c) notifications fanned out to subscribers via on_notification
(d) server-initiated request invokes the registered on_request handler and sends back
    {jsonrpc:'2.0', id, result}
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from amplifier_agent_client.jsonrpc import JsonRpcClient


class StubTransport:
    """Minimal stub transport for testing: captures sent frames, exposes a
    method to simulate incoming frames from the 'server'."""

    def __init__(self) -> None:
        self.sent: list[Any] = []
        self._frame_callbacks: list[Callable[[Any], None]] = []

    def send(self, obj: Any) -> None:
        self.sent.append(obj)

    def on_frame(self, cb: Callable[[Any], None]) -> None:
        self._frame_callbacks.append(cb)

    def receive(self, obj: Any) -> None:
        """Simulate an incoming frame from the server."""
        for cb in self._frame_callbacks:
            cb(obj)


@pytest.mark.asyncio
async def test_call_resolves_on_matching_result() -> None:
    """(a) call() resolves when matching result arrives."""
    transport = StubTransport()
    client = JsonRpcClient(transport)

    task = asyncio.create_task(client.call("echo", {"msg": "hello"}))

    # Let the event loop run so call() can register the pending future
    await asyncio.sleep(0)

    # Verify a request was sent
    assert len(transport.sent) == 1
    sent = transport.sent[0]
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "echo"
    assert sent["params"] == {"msg": "hello"}
    req_id = sent["id"]
    assert isinstance(req_id, int)

    # Simulate server sending back a result
    transport.receive({"jsonrpc": "2.0", "id": req_id, "result": {"echo": "hello"}})

    result = await task
    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_two_concurrent_calls_do_not_interfere() -> None:
    """(b) Two concurrent calls do not interfere (NC-L16 designed out)."""
    transport = StubTransport()
    client = JsonRpcClient(transport)

    task1 = asyncio.create_task(client.call("method1", {"x": 1}))
    task2 = asyncio.create_task(client.call("method2", {"y": 2}))

    # Let the event loop run so both calls register
    await asyncio.sleep(0)

    assert len(transport.sent) == 2
    frame1 = transport.sent[0]
    frame2 = transport.sent[1]
    id1 = frame1["id"]
    id2 = frame2["id"]
    assert id1 != id2

    # Resolve in reverse order: task2 first, then task1
    transport.receive({"jsonrpc": "2.0", "id": id2, "result": "result2"})
    transport.receive({"jsonrpc": "2.0", "id": id1, "result": "result1"})

    r1 = await task1
    r2 = await task2
    assert r1 == "result1"
    assert r2 == "result2"


@pytest.mark.asyncio
async def test_notifications_fanned_out_to_subscribers() -> None:
    """(c) Notifications fanned out to all subscribers via on_notification."""
    transport = StubTransport()
    client = JsonRpcClient(transport)

    received1: list[Any] = []
    received2: list[Any] = []
    client.on_notification(lambda n: received1.append(n))
    client.on_notification(lambda n: received2.append(n))

    # Simulate server sending a notification (no id, has method)
    transport.receive(
        {
            "jsonrpc": "2.0",
            "method": "status_update",
            "params": {"status": "running"},
        }
    )

    assert len(received1) == 1
    assert len(received2) == 1
    assert received1[0] == {"method": "status_update", "params": {"status": "running"}}
    assert received2[0] == {"method": "status_update", "params": {"status": "running"}}


@pytest.mark.asyncio
async def test_server_request_dispatched_to_handler() -> None:
    """(d) Server-initiated request invokes on_request handler and sends result."""
    transport = StubTransport()
    client = JsonRpcClient(transport)

    async def approval_handler(params: Any) -> Any:
        return {"approved": True, "params": params}

    client.on_request("approval/request", approval_handler)

    # Simulate a server-initiated request
    transport.receive(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "approval/request",
            "params": {"action": "proceed"},
        }
    )

    # Give the async handler time to run
    await asyncio.sleep(0.01)

    assert len(transport.sent) == 1
    response = transport.sent[0]
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 99
    assert response["result"] == {"approved": True, "params": {"action": "proceed"}}


@pytest.mark.asyncio
async def test_unknown_server_method_returns_error_32601() -> None:
    """(d-error) Unknown server method returns -32601 error."""
    transport = StubTransport()
    _client = JsonRpcClient(transport)

    # Simulate a server-initiated request with no registered handler
    transport.receive(
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "unknown/method",
            "params": {},
        }
    )

    # Give the async handler time to run
    await asyncio.sleep(0.01)

    assert len(transport.sent) == 1
    response = transport.sent[0]
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 42
    assert response["error"]["code"] == -32601
