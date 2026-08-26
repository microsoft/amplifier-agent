"""TypedDict shapes for JSON-RPC method requests/responses.

Source of truth for the cross-language wire contract per design Appendix A.
All TypedDicts here are JSON-serializable via ``json.dumps`` / ``json.loads``.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

PROTOCOL_VERSION = "0.4.0"
"""Wire protocol version. Bump on breaking changes; semver applies.

0.4.0 — Additive: ``TurnSubmitResult`` now reports the turn's real token and
        cost usage (``tokensIn``, ``tokensOut``, ``cacheReadTokens``,
        ``cacheWriteTokens``, ``costUsd``), accumulated by the engine off the
        display event stream. The CLI's ``--output json`` envelope carries the
        same five fields in ``metadata`` on both the success and the error path.
        ``tokensIn`` is the CHARGED input total (gross input + cache writes;
        cache reads are already inside the gross figure and are NOT added
        again); ``costUsd`` is a decimal STRING or null, never a float.
        Additive in shape -- no existing field changed meaning -- but NOT
        optional for hosts: the version check is exact string equality
        (``wrapper == engine``), so an engine and a wrapper on different
        protocol versions REFUSE each other rather than negotiating down.
        Engine and both wrapper SDKs must therefore be released together.
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
    """Result returned by the ``turn/submit`` JSON-RPC method.

    The usage fields report what THIS turn actually consumed, summed by the
    engine across every LLM call the turn made -- including calls made by
    delegated sub-agents. They are turn-scoped, not session-cumulative.

    Zero is a legitimate value: a turn that never reached a provider really did
    spend nothing.
    """

    reply: str | None
    turnId: str
    sessionId: str  # SC-6
    #: CHARGED input tokens: gross input + cache writes. Per amplifier-core's
    #: PROVIDER_CONTRACT, a provider's input_tokens is ALREADY the gross total
    #: (fresh + cache reads combined), so cacheReadTokens is a reported subset of
    #: it, not a separate bucket -- adding it again roughly doubles a cache-heavy
    #: turn. Cache writes are the one bucket billed on top of the gross total.
    #: Derive fresh input as tokensIn - cacheReadTokens - cacheWriteTokens.
    tokensIn: int
    #: Output tokens generated across the turn.
    tokensOut: int
    #: The portion of tokensIn already counted in gross input that the provider
    #: served from its prompt cache. Reported for visibility; never added on top.
    cacheReadTokens: int
    #: Tokens written into the provider's prompt cache. Billed on top of gross
    #: input, so this IS a component of tokensIn rather than a subset of it.
    cacheWriteTokens: int
    #: Turn cost in USD as a decimal STRING (e.g. "0.0123"), or None when no
    #: provider reported a cost. A string, never a float: a float cannot hold a
    #: decimal money value exactly, and a host summing per-turn costs from
    #: floats accumulates drift it cannot see. None is not zero -- an honest
    #: "nobody reported a cost" beats a silently-wrong 0.
    costUsd: str | None
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
