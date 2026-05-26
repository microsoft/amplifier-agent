"""Tests for ``WireApprovalProvider`` (design §4.7, A3 — CR-2).

The shim bridges ``amplifier_core.ApprovalProvider`` to the wire and exposes
exactly three failure modes via ``AaaError`` with ``classification='approval'``:

* ``approval_translation_failed`` — request cannot be translated to wire shape.
* ``approval_timeout``           — host did not respond within ``timeout_seconds``.
* ``approval_protocol_violation``— host response does not conform to expected shape.

These tests exercise each error path and the happy path. They deliberately use
``MagicMock`` for the ``ApprovalRequest`` so the suite is decoupled from the
upstream ``amplifier_core`` pydantic model (the shim only touches
``action``, ``tool_name``, ``arguments`` attributes).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_fake_request() -> MagicMock:
    """Return a duck-typed ApprovalRequest stand-in matching the wire payload shape."""
    req = MagicMock()
    req.action = "allow"
    req.tool_name = "bash"
    req.arguments = {"command": "echo hello"}
    return req


@pytest.mark.asyncio
async def test_approval_translation_failed_on_unserializable_request() -> None:
    """An exception during request translation surfaces approval_translation_failed."""
    from amplifier_agent_lib.protocol.errors import AaaError
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    provider = WireApprovalProvider(
        approval_request_fn=AsyncMock(return_value={"approved": True}),
    )

    def _boom(_req: Any) -> dict[str, Any]:
        raise ValueError("bad shape")

    # Inject a translator that throws — emulates a request the shim cannot serialize.
    provider._translate_request = _boom  # type: ignore[method-assign]

    with pytest.raises(AaaError) as excinfo:
        await provider.request_approval(_make_fake_request())

    assert excinfo.value.code == "approval_translation_failed"
    assert excinfo.value.classification == "approval"
    assert excinfo.value.severity == "error"


@pytest.mark.asyncio
async def test_approval_timeout() -> None:
    """A slow wire response surfaces approval_timeout."""
    from amplifier_agent_lib.protocol.errors import AaaError
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    async def slow_fn(_payload: Any) -> Any:
        await asyncio.sleep(9999)

    provider = WireApprovalProvider(
        approval_request_fn=slow_fn,
        timeout_seconds=0.05,
    )

    with pytest.raises(AaaError) as excinfo:
        await provider.request_approval(_make_fake_request())

    assert excinfo.value.code == "approval_timeout"
    assert excinfo.value.classification == "approval"
    assert excinfo.value.severity == "error"


@pytest.mark.asyncio
async def test_approval_protocol_violation_on_bad_response() -> None:
    """An exception during response translation surfaces approval_protocol_violation."""
    from amplifier_agent_lib.protocol.errors import AaaError
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    provider = WireApprovalProvider(
        approval_request_fn=AsyncMock(return_value={"approved": True}),
    )

    def _boom(_resp: Any) -> Any:
        raise ValueError("bad response")

    provider._translate_response = _boom  # type: ignore[method-assign]

    with pytest.raises(AaaError) as excinfo:
        await provider.request_approval(_make_fake_request())

    assert excinfo.value.code == "approval_protocol_violation"
    assert excinfo.value.classification == "approval"
    assert excinfo.value.severity == "error"


@pytest.mark.asyncio
async def test_successful_approval_returns_response() -> None:
    """Happy path — translator chain runs end-to-end and returns the translated response."""
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    fake_response = MagicMock()
    fake_response.approved = True

    provider = WireApprovalProvider(
        approval_request_fn=AsyncMock(return_value={"approved": True}),
    )
    provider._translate_response = lambda _resp: fake_response  # type: ignore[method-assign]

    result = await provider.request_approval(_make_fake_request())

    assert result is fake_response
