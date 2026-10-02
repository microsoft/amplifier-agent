"""Validate the public event vocabulary and error record shape independently of engine record constructors."""

from datetime import datetime, timedelta
import json
import re

EVENT_TYPES = frozenset(
    {
        "turn_started",
        "output_delta",
        "reasoning_delta",
        "reasoning_final",
        "tool_call",
        "tool_result",
        "approval_request",
        "approval_decision",
        "progress",
        "usage",
        "terminal",
    }
)
OWNED = re.compile(r"(?:[a-z][a-z0-9-]*\.)+[a-z][a-z0-9_-]*")


def error_record(error):
    assert isinstance(error.code, str)
    assert isinstance(error.category, str)
    assert isinstance(error.message, str)
    assert error.message.strip()
    assert isinstance(error.remedy, str)
    assert error.remedy.strip()
    assert type(error.retryable) is bool
    if error.correlation_id is not None:
        assert isinstance(error.correlation_id, str)
    if error.details is not None:
        json.dumps(error.details, allow_nan=False)


def event_record(event):
    assert event.contract_version == "turn-events/1"
    assert isinstance(event.session_id, str)
    assert isinstance(event.turn_id, str)
    assert type(event.sequence) is int
    assert event.sequence > 0
    assert event.type in EVENT_TYPES or OWNED.fullmatch(event.type)
    if event.at is not None:
        assert isinstance(event.at, datetime)
        assert event.at.tzinfo is not None
        assert event.at.utcoffset() == timedelta(0)
    if event.type == "progress":
        json.dumps(event.payload.data, allow_nan=False)
    if event.type == "terminal" and event.payload.error is not None:
        error_record(event.payload.error)
    if event.type == "tool_result" and event.payload.resolution.error is not None:
        error_record(event.payload.resolution.error)
