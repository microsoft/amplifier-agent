"""Tests for amplifier_agent_client.version: check_protocol_version().

TDD bullets (11b):
- match → ok=True
- mismatch → ok=False with code='protocol_version_mismatch' and remediation
- allowSkew=True → ok=True even on mismatch
"""

from __future__ import annotations

WRAPPER_VERSION = "0.1.0"


def test_check_protocol_version_match_returns_ok() -> None:
    """check_protocol_version returns ok=True when wrapper and engine versions match."""
    from amplifier_agent_client.version import check_protocol_version

    result = check_protocol_version(wrapper=WRAPPER_VERSION, engine=WRAPPER_VERSION)
    assert result.ok is True


def test_check_protocol_version_mismatch_returns_error() -> None:
    """check_protocol_version returns ok=False with protocol_version_mismatch on mismatch."""
    from amplifier_agent_client.version import check_protocol_version

    result = check_protocol_version(wrapper=WRAPPER_VERSION, engine="2026-04-aaa-v0")
    assert result.ok is False
    assert result.code == "protocol_version_mismatch"
    assert result.remediation is not None
    # Remediation should mention allow-protocol-skew or install
    assert "allow-protocol-skew" in result.remediation.lower() or "install" in result.remediation.lower()


def test_check_protocol_version_allow_skew_returns_ok() -> None:
    """check_protocol_version returns ok=True when allow_skew=True even on mismatch."""
    from amplifier_agent_client.version import check_protocol_version

    result = check_protocol_version(wrapper=WRAPPER_VERSION, engine="2026-04-aaa-v0", allow_skew=True)
    assert result.ok is True
