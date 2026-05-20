"""Tests for ErrorCode StrEnum — wire-level JSON-RPC error.data.code values."""

from __future__ import annotations

from enum import StrEnum


def test_error_code_is_str_enum() -> None:
    """ErrorCode members are strings; value matches the string form."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    assert issubclass(ErrorCode, StrEnum)
    # Spot-check: member is str and value matches
    assert isinstance(ErrorCode.INVALID_SESSION, str)
    assert ErrorCode.INVALID_SESSION.value == "invalid_session"
    assert ErrorCode.INTERNAL.value == "internal"


def test_error_code_values_unique() -> None:
    """No two ErrorCode members share the same value."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    values = [member.value for member in ErrorCode]
    assert len(values) == len(set(values)), "Duplicate ErrorCode values detected"


def test_error_code_required_members_present() -> None:
    """All 16 required error code members are present."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    required = {
        # Lifecycle/session
        "AGENT_NOT_READY",
        "INVALID_SESSION",
        "STALE_SESSION",
        "SESSION_NOT_FOUND",
        # Configuration
        "CONFIG_VALIDATION",
        "PROVIDER_NOT_CONFIGURED",
        "PROVIDER_INIT_FAILED",
        "PROMPT_REQUIRED",
        # Bundle/spawn
        "BUNDLE_LOAD_FAILED",
        "SPAWN_FAILED",
        # Approval
        "APPROVAL_DENIED",
        "APPROVAL_TIMEOUT",
        # Tool/runtime
        "TOOL_EXECUTION_FAILED",
        "RUNTIME",
        # Wire protocol
        "WIRE_PROTOCOL_VIOLATION",
        # Catch-all
        "INTERNAL",
    }
    actual = {member.name for member in ErrorCode}
    missing = required - actual
    assert not missing, f"Missing ErrorCode members: {missing}"


def test_error_code_lookup_by_value() -> None:
    """ErrorCode('invalid_session') resolves to ErrorCode.INVALID_SESSION."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    result = ErrorCode("invalid_session")
    assert result is ErrorCode.INVALID_SESSION
