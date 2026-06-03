"""TypedDict shapes for JSON-RPC method requests/responses.

Source of truth for the cross-language wire contract per design Appendix A.
All TypedDicts here are JSON-serializable via ``json.dumps`` / ``json.loads``.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

PROTOCOL_VERSION = "0.3.0"
"""Wire protocol version. Bump on breaking changes; semver applies.

0.2.0 — MCP config delivery changed from inline ``mcpServers`` dict to a
        path string (``mcpConfigPath``) pointing at a JSON file in the format
        documented by amplifier-module-tool-mcp (top-level ``mcpServers`` key).
        The engine sets ``AMPLIFIER_MCP_CONFIG`` from this path; the module
        reads it via its standard config discovery (config.py priority chain).
        See _runtime.py for the host-side semantics.
0.1.0 — Initial Mode A v2 protocol.
"""


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
# MCP host extensions (v0.1.0, design §4.10.1)
# ---------------------------------------------------------------------------


class McpServerConfig(TypedDict):
    """Per-server MCP configuration passed via ``initialize.params.mcpServers``.

    ``transport`` selects the wire transport; one of ``"stdio"``, ``"sse"``,
    or ``"streamable_http"``. Remaining fields are transport-specific and
    therefore optional at the TypedDict level — validation happens server-side.
    """

    transport: str
    command: NotRequired[str]
    args: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]
    url: NotRequired[str]
    headers: NotRequired[dict[str, str]]


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
    # MCP config: pass a path to a JSON file in the format documented by
    # amplifier-module-tool-mcp (top-level "mcpServers" key). The engine
    # sets AMPLIFIER_MCP_CONFIG from this path; the module reads it via its
    # standard config discovery. The wrapper handles dict-to-file
    # translation for hosts that prefer the inline-dict API.
    mcpConfigPath: NotRequired[str]


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
    sessionId: str  # SC-6
    finalEvent: NotRequired[dict[str, Any]]


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
