"""Tests for protocol_points/base.py — ApprovalSystem and DisplaySystem Protocols."""

from __future__ import annotations

import pytest


def test_display_system_protocol_conformance() -> None:
    """A class with a sync emit() method satisfies the DisplaySystem Protocol."""
    from amplifier_agent_lib.protocol_points.base import DisplayEvent, DisplaySystem

    class _RecordingDisplay:
        def __init__(self) -> None:
            self.events: list[DisplayEvent] = []

        def emit(self, event: DisplayEvent) -> None:
            self.events.append(event)

    recorder = _RecordingDisplay()
    assert isinstance(recorder, DisplaySystem), "_RecordingDisplay should conform to DisplaySystem"
    event: DisplayEvent = {"type": "result/delta", "sessionId": "sess-1"}
    recorder.emit(event)
    assert recorder.events == [event]


@pytest.mark.asyncio
async def test_approval_system_protocol_conformance() -> None:
    """A class with an async request() method satisfies the ApprovalSystem Protocol."""
    from amplifier_agent_lib.protocol_points.base import ApprovalRequest, ApprovalResponse, ApprovalSystem

    class _AlwaysAcceptApproval:
        async def request(self, req: ApprovalRequest) -> ApprovalResponse:
            return {"action": "accept"}

    approval = _AlwaysAcceptApproval()
    assert isinstance(approval, ApprovalSystem), "_AlwaysAcceptApproval should conform to ApprovalSystem"
    req: ApprovalRequest = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "approvalId": "approval-1",
        "kind": "tool_call",
        "payload": {},
        "timeoutMs": 5000,
    }
    response = await approval.request(req)
    assert response["action"] == "accept"


def test_approval_action_values() -> None:
    """ApprovalAction Literal contains exactly accept, decline, cancel."""
    import typing

    from amplifier_agent_lib.protocol_points.base import ApprovalAction

    args = typing.get_args(ApprovalAction)
    assert set(args) == {"accept", "decline", "cancel"}, f"Expected {{accept, decline, cancel}}, got {set(args)!r}"


def test_display_event_type_discriminator() -> None:
    """DisplayEvent carries a required 'type' field and an optional 'turnId'."""
    from amplifier_agent_lib.protocol_points.base import DisplayEvent

    # Basic event without turnId
    event: DisplayEvent = {"type": "result/delta", "sessionId": "sess-1"}
    assert event["type"] == "result/delta"

    # Event with optional turnId
    event_with_turn: DisplayEvent = {"type": "tool/started", "sessionId": "sess-1", "turnId": "turn-42"}
    assert event_with_turn["turnId"] == "turn-42"


def test_no_spawn_protocol_exported() -> None:
    """SpawnSystem, Spawn, and SpawnProtocol must NOT appear in protocol_points.base."""
    import amplifier_agent_lib.protocol_points.base as _base

    forbidden = ("SpawnSystem", "Spawn", "SpawnProtocol")
    module_names = dir(_base)
    for name in forbidden:
        assert name not in module_names, f"{name!r} must not be exported from protocol_points.base"
