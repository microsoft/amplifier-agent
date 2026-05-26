"""Tests for v0.1.0 AaaError extensions and new ErrorCode values (A1).

Per design §4.10.2, AaaError gains optional severity/classification/correlation_id/
stderr_tail fields, and ErrorCode gains APPROVAL_TRANSLATION_FAILED,
APPROVAL_PROTOCOL_VIOLATION, and ENV_INJECTION_REJECTED.
"""

from __future__ import annotations


def test_aaa_error_has_severity_field() -> None:
    """AaaError accepts a keyword severity argument and exposes it."""
    from amplifier_agent_lib.protocol.errors import AaaError

    err = AaaError(code="approval_timeout", message="timed out", severity="error")
    assert err.severity == "error"


def test_aaa_error_has_classification_field() -> None:
    """AaaError accepts a keyword classification argument and exposes it."""
    from amplifier_agent_lib.protocol.errors import AaaError

    err = AaaError(code="approval_timeout", message="timed out", classification="approval")
    assert err.classification == "approval"


def test_aaa_error_has_correlation_id_field() -> None:
    """AaaError accepts a keyword correlation_id argument and exposes it."""
    from amplifier_agent_lib.protocol.errors import AaaError

    err = AaaError(code="approval_timeout", message="timed out", correlation_id="req-abc")
    assert err.correlation_id == "req-abc"


def test_error_code_has_approval_translation_failed() -> None:
    """ErrorCode.APPROVAL_TRANSLATION_FAILED has the canonical wire value."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    assert ErrorCode.APPROVAL_TRANSLATION_FAILED.value == "approval_translation_failed"


def test_error_code_has_approval_protocol_violation() -> None:
    """ErrorCode.APPROVAL_PROTOCOL_VIOLATION has the canonical wire value."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    assert ErrorCode.APPROVAL_PROTOCOL_VIOLATION.value == "approval_protocol_violation"


def test_error_code_has_env_injection_rejected() -> None:
    """ErrorCode.ENV_INJECTION_REJECTED has the canonical wire value."""
    from amplifier_agent_lib.protocol.errors import ErrorCode

    assert ErrorCode.ENV_INJECTION_REJECTED.value == "env_injection_rejected"


def test_aaa_error_defaults_optional_fields_to_none() -> None:
    """Only code+message are required; optional fields default to None."""
    from amplifier_agent_lib.protocol.errors import AaaError

    err = AaaError(code="internal", message="boom")
    assert err.severity is None
    assert err.classification is None
    assert err.correlation_id is None
    assert err.stderr_tail is None
