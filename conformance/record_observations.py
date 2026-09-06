"""Validate public event and error vocabularies independently of engine record constructors."""

import json
import re
from dataclasses import is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from amplifier_agent import AgentError

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
ERROR_CODES = frozenset(
    {
        "closed",
        "selector_rejected",
        "session_id_invalid",
        "already_exists",
        "not_found",
        "session_in_use",
        "busy",
        "stream_already_consumed",
        "turn_cancelled",
        "invalid_input",
        "tool_callback_failed",
        "tool_result_invalid",
        "tool_failed",
        "tool_completion_unknown",
        "tool_recovery_blocked",
        "approval_denied",
        "approval_cancelled",
        "approval_timeout",
        "approval_unavailable",
        "approval_invalid",
        "provider_failed",
        "internal_failed",
        "contract_version_mismatch",
        "engine_unavailable",
    }
)
ERROR_CATEGORIES = frozenset(
    {
        "lifecycle",
        "selection",
        "session",
        "turn",
        "input",
        "executor",
        "approval",
        "provider",
        "internal",
    }
)
OWNED = re.compile(r"(?:[a-z][a-z0-9-]*\.)+[a-z][a-z0-9_-]*")


def observation(value):
    if is_dataclass(value) or isinstance(value, AgentError):
        return {key: observation(item) for key, item in vars(value).items() if item is not None}
    if isinstance(value, dict):
        return {key: observation(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [observation(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return value


def error_record(error):
    assert isinstance(error.code, str)
    assert error.code in ERROR_CODES or OWNED.fullmatch(error.code)
    assert error.category in ERROR_CATEGORIES
    assert isinstance(error.message, str) and error.message.strip()
    assert isinstance(error.remedy, str) and error.remedy.strip()
    assert type(error.retryable) is bool
    if error.correlation_id is not None:
        assert isinstance(error.correlation_id, str)
    if error.details is not None:
        json.dumps(error.details, allow_nan=False)


def event_record(event):
    assert event.contract_version == "turn-events/1"
    assert isinstance(event.session_id, str) and isinstance(event.turn_id, str)
    assert type(event.sequence) is int and event.sequence > 0
    assert event.type in EVENT_TYPES or OWNED.fullmatch(event.type)
    if event.at is not None:
        assert isinstance(event.at, datetime)
        assert event.at.tzinfo is not None and event.at.utcoffset() == timedelta(0)
    if event.type == "progress":
        json.dumps(event.payload.data, allow_nan=False)
    if event.type == "terminal" and event.payload.error is not None:
        error_record(event.payload.error)
    if event.type == "tool_result" and event.payload.resolution.error is not None:
        error_record(event.payload.resolution.error)
