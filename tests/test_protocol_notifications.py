"""Tests for notification TypedDicts and CANONICAL_DISPLAY_EVENTS constant."""

from __future__ import annotations

import json


def test_canonical_display_events_length() -> None:
    """CANONICAL_DISPLAY_EVENTS must contain exactly 9 events."""
    from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS

    assert len(CANONICAL_DISPLAY_EVENTS) == 9


def test_canonical_display_events_set_equality() -> None:
    """CANONICAL_DISPLAY_EVENTS must match the design §6 canonical taxonomy exactly."""
    from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS

    expected = {
        "result/delta",
        "result/final",
        "tool/started",
        "tool/completed",
        "progress",
        "thinking/delta",
        "thinking/final",
        "usage",
        "error",
    }
    assert set(CANONICAL_DISPLAY_EVENTS) == expected


def test_module_docstring_contains_l14_synthesis_contract() -> None:
    """Module docstring must contain 'L14', 'result/final', and 'synthesi'."""
    import amplifier_agent_lib.protocol.notifications as mod

    doc = mod.__doc__ or ""
    assert "L14" in doc, "Module docstring must contain 'L14'"
    assert "result/final" in doc, "Module docstring must contain 'result/final'"
    assert "synthesi" in doc, "Module docstring must contain 'synthesi' (lowercase)"


def test_result_delta_notification_roundtrip() -> None:
    """ResultDeltaNotification roundtrips through json.dumps/loads preserving required fields."""
    from amplifier_agent_lib.protocol.notifications import ResultDeltaNotification

    event: ResultDeltaNotification = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "text": "Hello",
    }
    roundtripped = json.loads(json.dumps(event))
    assert roundtripped["sessionId"] == "sess-1"
    assert roundtripped["turnId"] == "turn-1"
    assert roundtripped["text"] == "Hello"


def test_result_final_notification_roundtrip() -> None:
    """ResultFinalNotification roundtrips; usage is NotRequired."""
    from amplifier_agent_lib.protocol.notifications import ResultFinalNotification

    # Without usage
    event_no_usage: ResultFinalNotification = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "text": "Done",
    }
    rt = json.loads(json.dumps(event_no_usage))
    assert rt["text"] == "Done"
    assert "usage" not in rt

    # With usage
    event_with_usage: ResultFinalNotification = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "text": "Done",
        "usage": {"inputTokens": 10, "outputTokens": 20},
    }
    rt2 = json.loads(json.dumps(event_with_usage))
    assert rt2["usage"]["inputTokens"] == 10


def test_tool_started_notification_roundtrip() -> None:
    """ToolStartedNotification roundtrips preserving all required fields."""
    from amplifier_agent_lib.protocol.notifications import ToolStartedNotification

    event: ToolStartedNotification = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "toolCallId": "call-42",
        "name": "bash",
        "args": {"command": "echo hi"},
    }
    rt = json.loads(json.dumps(event))
    assert rt["toolCallId"] == "call-42"
    assert rt["name"] == "bash"
    assert rt["args"] == {"command": "echo hi"}


def test_tool_completed_notification_roundtrip() -> None:
    """ToolCompletedNotification roundtrips preserving all required fields."""
    from amplifier_agent_lib.protocol.notifications import ToolCompletedNotification

    event: ToolCompletedNotification = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "toolCallId": "call-42",
        "name": "bash",
        "result": {"stdout": "hi\n"},
        "durationMs": 123,
    }
    rt = json.loads(json.dumps(event))
    assert rt["durationMs"] == 123
    assert rt["result"] == {"stdout": "hi\n"}


def test_thinking_and_progress_notifications_roundtrip() -> None:
    """ThinkingDelta, ThinkingFinal, and ProgressNotification roundtrip correctly."""
    from amplifier_agent_lib.protocol.notifications import (
        ProgressNotification,
        ThinkingDeltaNotification,
        ThinkingFinalNotification,
    )

    delta: ThinkingDeltaNotification = {"sessionId": "s", "turnId": "t", "text": "hmm"}
    final: ThinkingFinalNotification = {"sessionId": "s", "turnId": "t", "text": "done thinking"}
    progress: ProgressNotification = {"sessionId": "s", "turnId": "t", "message": "Step 1", "percent": 50.0}

    for ev in [delta, final, progress]:
        rt = json.loads(json.dumps(ev))
        assert rt["sessionId"] == "s"

    rt_progress = json.loads(json.dumps(progress))
    assert rt_progress["percent"] == 50.0

    # NotRequired: progress without percent
    progress_no_pct: ProgressNotification = {"sessionId": "s", "turnId": "t", "message": "Step 1"}
    rt_np = json.loads(json.dumps(progress_no_pct))
    assert "percent" not in rt_np


def test_usage_notification_roundtrip() -> None:
    """UsageNotification roundtrips; cost is NotRequired."""
    from amplifier_agent_lib.protocol.notifications import UsageNotification

    event: UsageNotification = {
        "sessionId": "s",
        "turnId": "t",
        "inputTokens": 100,
        "outputTokens": 200,
    }
    rt = json.loads(json.dumps(event))
    assert rt["inputTokens"] == 100
    assert rt["outputTokens"] == 200
    assert "cost" not in rt

    event_with_cost: UsageNotification = {
        "sessionId": "s",
        "turnId": "t",
        "inputTokens": 100,
        "outputTokens": 200,
        "cost": 0.0015,
    }
    rt2 = json.loads(json.dumps(event_with_cost))
    assert rt2["cost"] == 0.0015


def test_error_notification_roundtrip() -> None:
    """ErrorNotification roundtrips; turnId is NotRequired."""
    from amplifier_agent_lib.protocol.notifications import ErrorNotification

    # Without turnId
    event_no_turn: ErrorNotification = {
        "sessionId": "s",
        "code": "internal",
        "message": "Oops",
        "recoverable": False,
    }
    rt = json.loads(json.dumps(event_no_turn))
    assert rt["code"] == "internal"
    assert rt["recoverable"] is False
    assert "turnId" not in rt

    # With turnId
    event_with_turn: ErrorNotification = {
        "sessionId": "s",
        "turnId": "t",
        "code": "runtime",
        "message": "Tool failed",
        "recoverable": True,
    }
    rt2 = json.loads(json.dumps(event_with_turn))
    assert rt2["turnId"] == "t"


def test_approval_notifications_roundtrip() -> None:
    """ApprovalRequestNotification and ApprovalTimeoutNotification roundtrip correctly."""
    from amplifier_agent_lib.protocol.notifications import (
        ApprovalRequestNotification,
        ApprovalTimeoutNotification,
    )

    req: ApprovalRequestNotification = {
        "sessionId": "s",
        "turnId": "t",
        "approvalId": "appr-1",
        "kind": "tool_call",
        "payload": {"tool": "bash", "args": {"command": "rm -rf /"}},
        "timeoutMs": 30000,
    }
    rt = json.loads(json.dumps(req))
    assert rt["approvalId"] == "appr-1"
    assert rt["timeoutMs"] == 30000

    timeout: ApprovalTimeoutNotification = {
        "sessionId": "s",
        "turnId": "t",
        "approvalId": "appr-1",
        "kind": "tool_call",
    }
    rt2 = json.loads(json.dumps(timeout))
    assert rt2["kind"] == "tool_call"


def test_usage_notification_has_enrichment_fields() -> None:
    """UsageNotification must declare cost as NotRequired[str] plus the 7 enrichment fields."""
    from typing import get_args, get_type_hints

    from amplifier_agent_lib.protocol.notifications import UsageNotification

    hints = get_type_hints(UsageNotification, include_extras=True)

    # cost must be a string slot (Decimal-as-string), NOT a float.
    assert "cost" in hints
    assert get_args(hints["cost"]) == (str,), "cost must be NotRequired[str], not float"

    # All new optional fields must be present.
    expected_str_fields = {"model", "provider", "sessionCostTotal", "agentName"}
    expected_int_fields = {"llmDurationMs", "cacheReadTokens", "cacheWriteTokens"}
    for field in expected_str_fields:
        assert field in hints, f"missing {field}"
        assert get_args(hints[field]) == (str,), f"{field} must be NotRequired[str]"
    for field in expected_int_fields:
        assert field in hints, f"missing {field}"
        assert get_args(hints[field]) == (int,), f"{field} must be NotRequired[int]"

    # The two existing required fields remain required.
    assert "inputTokens" in hints
    assert "outputTokens" in hints
