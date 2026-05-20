"""TypedDict shapes for JSON-RPC method requests/responses.

Source of truth for the cross-language wire contract per design Appendix A.
All TypedDicts here are JSON-serializable via ``json.dumps`` / ``json.loads``.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

PROTOCOL_VERSION = "2026-05-aaa-v0"
"""Wire protocol version. Bump on breaking changes; semver applies."""


class ClientInfo(TypedDict):
    """Identity of the connecting client."""

    name: str
    version: str


class ServerInfo(TypedDict):
    """Identity of the agent server."""

    name: str
    version: str


class SessionState(TypedDict):
    """Returned session state after initialize or session/create."""

    sessionId: str
    resumed: bool


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class InitializeParams(TypedDict):
    """Parameters for the ``initialize`` JSON-RPC method."""

    protocolVersion: str
    clientInfo: ClientInfo
    capabilities: dict[str, Any]
    sessionId: NotRequired[str]
    resume: NotRequired[bool]
    providerOverride: NotRequired[str]
    cwd: NotRequired[str]


class InitializeResult(TypedDict):
    """Result returned by the ``initialize`` JSON-RPC method."""

    capabilities: dict[str, Any]
    serverInfo: ServerInfo
    sessionState: SessionState


# ---------------------------------------------------------------------------
# turn/submit
# ---------------------------------------------------------------------------


class TurnSubmitParams(TypedDict):
    """Parameters for the ``turn/submit`` JSON-RPC method."""

    sessionId: str
    turnId: str
    prompt: str
    attachments: NotRequired[list[dict[str, Any]]]


class TurnSubmitResult(TypedDict):
    """Result returned by the ``turn/submit`` JSON-RPC method."""

    reply: str | None
    turnId: str
    finalEvent: NotRequired[dict[str, Any]]


# ---------------------------------------------------------------------------
# turn/cancel
# ---------------------------------------------------------------------------


class TurnCancelParams(TypedDict):
    """Parameters for the ``turn/cancel`` JSON-RPC method."""

    sessionId: str
    turnId: str


class TurnCancelResult(TypedDict):
    """Result returned by the ``turn/cancel`` JSON-RPC method."""

    cancelled: bool


# ---------------------------------------------------------------------------
# session/create
# ---------------------------------------------------------------------------


class SessionCreateParams(TypedDict):
    """Parameters for the ``session/create`` JSON-RPC method."""

    sessionId: str
    resume: NotRequired[bool]


class SessionCreateResult(TypedDict):
    """Result returned by the ``session/create`` JSON-RPC method."""

    sessionState: SessionState


# ---------------------------------------------------------------------------
# session/end
# ---------------------------------------------------------------------------


class SessionEndParams(TypedDict):
    """Parameters for the ``session/end`` JSON-RPC method."""

    sessionId: str


class SessionEndResult(TypedDict):
    """Result returned by the ``session/end`` JSON-RPC method."""

    ended: bool


# ---------------------------------------------------------------------------
# agent/shutdown
# ---------------------------------------------------------------------------


class AgentShutdownParams(TypedDict):
    """Parameters for the ``agent/shutdown`` JSON-RPC method (none required)."""


class AgentShutdownResult(TypedDict):
    """Result returned by the ``agent/shutdown`` JSON-RPC method (none required)."""


# ---------------------------------------------------------------------------
# cache/info
# ---------------------------------------------------------------------------


class CacheInfoParams(TypedDict):
    """Parameters for the ``cache/info`` JSON-RPC method (none required)."""


class CacheInfoResult(TypedDict):
    """Result returned by the ``cache/info`` JSON-RPC method."""

    cachePath: str
    preparedBundles: list[str]
