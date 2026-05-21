"""Tests for the approval bridge — in-band, mid-turn JSON-RPC round-trip (§5.2).

RED: fails because wrappers/python/src/amplifier_agent_client/approval.py does not exist yet.
GREEN: passes once make_approval_handler is implemented and wired into session.py.

Three async cases:
(a) forwards request to adapter and returns its response {decision:'allow', requestId}
(b) emits decision='timeout' if on_request exceeds timeout_ms (never-resolving coroutine, timeout_ms=50)
(c) falls back to {decision:'deny'} when no adapter configured (on_request=None)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from amplifier_agent_client.approval import make_approval_handler


@pytest.mark.asyncio
async def test_forwards_request_to_adapter_and_returns_response() -> None:
    """(a) forwards request to adapter and returns its response."""

    async def on_request(params: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "allow", "requestId": params["id"]}

    handler = make_approval_handler(on_request=on_request, timeout_ms=1000)
    result = await handler({"id": "req-1", "tool": "bash", "args": {}})
    assert result["decision"] == "allow"
    assert result["requestId"] == "req-1"


@pytest.mark.asyncio
async def test_timeout_if_on_request_exceeds_timeout_ms() -> None:
    """(b) emits decision='timeout' if on_request exceeds timeout_ms."""

    async def on_request(params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(10)  # much longer than timeout_ms=50ms
        return {"decision": "allow"}

    handler = make_approval_handler(on_request=on_request, timeout_ms=50)
    result = await handler({"id": "req-2", "tool": "bash", "args": {}})
    assert result["decision"] == "timeout"


@pytest.mark.asyncio
async def test_deny_when_no_adapter_configured() -> None:
    """(c) falls back to deny when no adapter configured (on_request=None)."""
    handler = make_approval_handler(on_request=None, timeout_ms=1000)
    result = await handler({"id": "req-3", "tool": "bash", "args": {}})
    assert result["decision"] == "deny"
    assert result.get("reason") == "no_adapter_configured"
