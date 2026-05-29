"""Test PROTOCOL_VERSION is bumped to 0.2.0 (path-based MCP config delivery)."""

from __future__ import annotations


def test_protocol_version_is_0_2_0() -> None:
    """PROTOCOL_VERSION must be '0.2.0' per the path-based MCP config bump (commit ea51d05)."""
    from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == "0.2.0"
