"""Test PROTOCOL_VERSION is bumped to 0.3.0 (host config layer + envelope cleanup release)."""

from __future__ import annotations


def test_protocol_version_is_0_3_0() -> None:
    """PROTOCOL_VERSION must be '0.3.0' per the 0.4.0 release bump.

    Wire envelope/initialize shape changed (hostCapabilities removed,
    mcpConfigPath replaced mcpServers, skills field added) -- backward
    incompatible at protocol 0.x.  Old wrappers pinned to '0.2.0' should
    hard-fail handshake against this engine.
    """
    from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == "0.3.0"
