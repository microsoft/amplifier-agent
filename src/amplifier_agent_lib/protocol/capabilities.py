"""Capability negotiation per design §6.

The engine ONLY delivers events and accepts approval actions that the client
has explicitly advertised in its ``ClientCapabilities``.  This module defines
the TypedDicts that represent those capability bundles and the negotiation
function that computes the effective intersection.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS


class ApprovalCapability(TypedDict):
    """Approval-flow capabilities: the approval action strings a party supports."""

    actions: list[str]


class DisplayCapability(TypedDict):
    """Display capabilities: the notification event types a party can handle."""

    events: list[str]


class ClientCapabilities(TypedDict, total=False):
    """Capabilities advertised by the connecting client."""

    approval: ApprovalCapability
    display: DisplayCapability
    experimental: NotRequired[dict[str, object]]


class ServerCapabilities(TypedDict, total=False):
    """Capabilities advertised by the agent server."""

    approval: ApprovalCapability
    display: DisplayCapability
    experimental: NotRequired[dict[str, object]]


def server_default_capabilities() -> ServerCapabilities:
    """Return the full default capabilities offered by the server.

    Approval actions: ``accept``, ``decline``, ``cancel``.
    Display events: the complete ``CANONICAL_DISPLAY_EVENTS`` taxonomy.
    """
    return {
        "approval": {"actions": ["accept", "decline", "cancel"]},
        "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
    }


def negotiate_capabilities(
    *,
    client: ClientCapabilities,
    server: ServerCapabilities,
) -> ServerCapabilities:
    """Return the negotiated capabilities — the intersection of client and server.

    Each field is the *sorted* intersection of what both sides advertise.
    If the client omits a section entirely, the effective list for that section
    is empty (the server will not send events the client never asked for).

    Parameters
    ----------
    client:
        Capabilities advertised by the client during ``initialize``.
    server:
        Capabilities the server is willing to provide (typically the result
        of :func:`server_default_capabilities`).

    Returns
    -------
    ServerCapabilities
        Negotiated capabilities — the engine should use this to filter outbound
        notifications and accepted approval actions.
    """
    # Compute approval actions intersection
    client_actions: list[str] = (client.get("approval") or {}).get("actions", [])
    server_actions: list[str] = (server.get("approval") or {}).get("actions", [])
    negotiated_actions = sorted(set(client_actions) & set(server_actions))

    # Compute display events intersection
    client_events: list[str] = (client.get("display") or {}).get("events", [])
    server_events: list[str] = (server.get("display") or {}).get("events", [])
    negotiated_events = sorted(set(client_events) & set(server_events))

    return {
        "approval": {"actions": negotiated_actions},
        "display": {"events": negotiated_events},
    }
