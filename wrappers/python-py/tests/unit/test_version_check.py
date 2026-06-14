"""Unit tests for check_protocol_version."""

from __future__ import annotations

from amplifier_agent_py import (
    VersionCheckFail,
    VersionCheckOk,
    check_protocol_version,
)


def test_match_returns_ok() -> None:
    result = check_protocol_version(wrapper="0.3.0", engine="0.3.0")
    assert isinstance(result, VersionCheckOk)
    assert result.ok is True


def test_mismatch_returns_fail_with_remediation() -> None:
    result = check_protocol_version(wrapper="0.3.0", engine="0.2.0")
    assert isinstance(result, VersionCheckFail)
    assert result.ok is False
    assert result.code == "protocol_version_mismatch"
    assert "0.3.0" in result.remediation
    assert "0.2.0" in result.remediation


def test_allow_skew_bypasses_mismatch() -> None:
    result = check_protocol_version(wrapper="0.3.0", engine="0.2.0", allow_skew=True)
    assert isinstance(result, VersionCheckOk)


def test_allow_skew_returns_ok_even_on_match() -> None:
    result = check_protocol_version(wrapper="0.3.0", engine="0.3.0", allow_skew=True)
    assert isinstance(result, VersionCheckOk)
