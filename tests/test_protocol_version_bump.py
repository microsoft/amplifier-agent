"""Test PROTOCOL_VERSION is bumped to 0.1.0 (A1)."""

from __future__ import annotations


def test_protocol_version_is_0_1_0() -> None:
    """PROTOCOL_VERSION must be '0.1.0' per design §4.10.3 (A1)."""
    from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == "0.1.0"
