"""Tests for capability negotiation and protocol package re-exports."""

from __future__ import annotations


def test_protocol_version_reexported() -> None:
    """PROTOCOL_VERSION is accessible via the top-level protocol package."""
    from amplifier_agent_lib.protocol import PROTOCOL_VERSION

    assert isinstance(PROTOCOL_VERSION, str)
    assert PROTOCOL_VERSION  # non-empty


def test_error_code_reexported() -> None:
    """ErrorCode is accessible via the top-level protocol package."""
    from amplifier_agent_lib.protocol import ErrorCode

    assert ErrorCode.INTERNAL.value == "internal"


def test_server_default_capabilities_shape() -> None:
    """server_default_capabilities returns all 9 canonical display events and 3 approval actions."""
    from amplifier_agent_lib.protocol import server_default_capabilities

    caps = server_default_capabilities()

    # Must contain approval with exactly 3 actions
    approval = caps.get("approval")
    assert approval is not None
    actions = approval["actions"]
    assert sorted(actions) == ["accept", "cancel", "decline"]

    # Must contain display with exactly 9 canonical events
    display = caps.get("display")
    assert display is not None
    events = display["events"]
    expected_events = {
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
    assert set(events) == expected_events
    assert len(events) == 9


def test_negotiate_intersects_display_events() -> None:
    """negotiate_capabilities returns the sorted intersection of display events."""
    from amplifier_agent_lib.protocol import (
        ClientCapabilities,
        ServerCapabilities,
        negotiate_capabilities,
    )

    client: ClientCapabilities = {
        "display": {"events": ["result/delta", "result/final", "unknown/event"]},
        "approval": {"actions": ["accept", "decline", "cancel"]},
    }
    server: ServerCapabilities = {
        "display": {
            "events": [
                "result/delta",
                "result/final",
                "tool/started",
                "tool/completed",
                "progress",
                "thinking/delta",
                "thinking/final",
                "usage",
                "error",
            ]
        },
        "approval": {"actions": ["accept", "decline", "cancel"]},
    }

    negotiated = negotiate_capabilities(client=client, server=server)

    # Only events advertised by both sides; "unknown/event" is excluded
    display = negotiated.get("display")
    assert display is not None
    assert display["events"] == sorted(["result/delta", "result/final"])


def test_negotiate_intersects_approval_actions() -> None:
    """negotiate_capabilities returns the sorted intersection of approval actions."""
    from amplifier_agent_lib.protocol import (
        ClientCapabilities,
        ServerCapabilities,
        negotiate_capabilities,
    )

    client: ClientCapabilities = {
        "approval": {"actions": ["accept"]},
        "display": {"events": ["result/delta"]},
    }
    server: ServerCapabilities = {
        "approval": {"actions": ["accept", "decline", "cancel"]},
        "display": {"events": ["result/delta", "result/final"]},
    }

    negotiated = negotiate_capabilities(client=client, server=server)

    approval = negotiated.get("approval")
    assert approval is not None
    assert approval["actions"] == ["accept"]


def test_negotiate_handles_missing_client_sections() -> None:
    """negotiate_capabilities returns empty lists when client omits approval or display sections."""
    from amplifier_agent_lib.protocol import (
        ClientCapabilities,
        ServerCapabilities,
        negotiate_capabilities,
    )

    # Completely empty client capabilities (total=False TypedDict means all keys optional)
    client: ClientCapabilities = {}
    server: ServerCapabilities = {
        "approval": {"actions": ["accept", "decline", "cancel"]},
        "display": {"events": ["result/delta", "result/final"]},
    }

    negotiated = negotiate_capabilities(client=client, server=server)

    approval = negotiated.get("approval")
    assert approval is not None
    assert approval["actions"] == []

    display = negotiated.get("display")
    assert display is not None
    assert display["events"] == []
