"""Public dataclass types for the Amplifier Agent Python wrapper.

DisplayEvent variants, EngineInfo, McpServerConfig, and helpers that the
public API exposes.  Mirrors the TypeScript wrapper's `DisplayEvent` discriminated
union and the schema-generated wire types.

DisplayEvent is a tagged union of frozen dataclasses; consumers should switch on
the ``type`` field literal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import Classification, Severity

# ---------------------------------------------------------------------------
# DisplayEvent variants (mirror wrappers/typescript/src/session.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class InitEvent:
    """Yielded synchronously before the engine subprocess is spawned (SC-1)."""

    session_id: str
    type: Literal["init"] = "init"


@dataclass(frozen=True, kw_only=True)
class ActivityEvent:
    """Yielded every 2 seconds while the subprocess is alive (stuck-detection signal)."""

    type: Literal["activity"] = "activity"


@dataclass(frozen=True, kw_only=True)
class ResultEvent:
    """Yielded once when the subprocess emits a successful §4.1 envelope."""

    text: str
    type: Literal["result"] = "result"


@dataclass(frozen=True, kw_only=True)
class ErrorEvent:
    """Yielded once when the subprocess errors, hangs, or fails to spawn."""

    code: str
    classification: Classification
    severity: Severity
    correlation_id: str
    message: str
    retryable: bool
    stderr_tail: str | None = None
    type: Literal["error"] = "error"


@dataclass(frozen=True, kw_only=True)
class NotificationEvent:
    """Wire-protocol notification dispatched from the engine's stderr NDJSON stream.

    `method` is the JSON-RPC method name verbatim from the wire envelope
    (e.g. ``"progress"``, ``"tool/started"``).  `params` is the raw payload
    the engine emitted, unaltered.  Hosts can narrow on `method` and treat
    `params` as the typed shape from the JSON-RPC schemas.
    """

    method: str
    params: Any
    type: Literal["notification"] = "notification"


DisplayEvent = InitEvent | ActivityEvent | ResultEvent | ErrorEvent | NotificationEvent


# ---------------------------------------------------------------------------
# EngineInfo (mirror wrappers/typescript/src/session.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EngineInfo:
    """Engine metadata returned by ``SessionHandle.get_engine_info()`` (D5).

    Resolved at ``spawn_agent()`` time via the engine version probe (Issue #9).
    """

    binary_path: str
    protocol_version: str
    engine_version: str
    bundle_digest: str


# ---------------------------------------------------------------------------
# Wire types (the subset surface used at construction time)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class McpServerConfig:
    """Per-server MCP configuration passed via ``mcp_servers``.

    Mirrors `McpServerConfig` from wrappers/typescript/src/types.ts.
    The wrapper spills the full map verbatim to a 0600 tmpfile; no field is
    inspected beyond presence.
    """

    transport: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Helper for converting McpServerConfig back to a plain dict (for spill).
# ---------------------------------------------------------------------------


def mcp_server_to_dict(cfg: McpServerConfig | dict[str, Any]) -> dict[str, Any]:
    """Convert McpServerConfig (or a raw dict) to a plain dict for serialization.

    Drops keys whose value is None so the spilled JSON matches what tool-mcp
    expects (which uses presence to discriminate transports).
    """
    if isinstance(cfg, dict):
        return {k: v for k, v in cfg.items() if v is not None}
    result: dict[str, Any] = {"transport": cfg.transport}
    if cfg.command is not None:
        result["command"] = cfg.command
    if cfg.args is not None:
        result["args"] = list(cfg.args)
    if cfg.env is not None:
        result["env"] = dict(cfg.env)
    if cfg.url is not None:
        result["url"] = cfg.url
    if cfg.headers is not None:
        result["headers"] = dict(cfg.headers)
    return result


# ---------------------------------------------------------------------------
# Approval policy shape (mirrors the TS surface, but onRequest is rejected).
# ---------------------------------------------------------------------------


ApprovalMode = Literal["yes", "no", "prompt"]


@dataclass(frozen=True, kw_only=True)
class ApprovalParams:
    """Approval policy (Issue #10).

    Only the static-policy shape (``mode``) is supported in v1.  The mid-turn
    ``on_request`` callback is rejected at ``spawn_agent()`` time because the
    Mode A v2 wire has no mid-turn host channel.  Mirrors the TS wrapper's
    SC-C check.
    """

    mode: ApprovalMode | None = None
    on_request: Any | None = None  # rejected at spawn_agent() time if set
    timeout_ms: int | None = None


@dataclass(frozen=True, kw_only=True)
class DisplayParams:
    """Display sink and subagent filter (mirrors TS DisplayParams)."""

    on_event: Any | None = None  # Callable[[DisplayEvent], None]
    subagent_events: Literal["all", "none"] | None = None


@dataclass(frozen=True, kw_only=True)
class EnvParams:
    """Environment filtering for the subprocess (mirrors TS env params)."""

    allowlist: list[str]
    extra: dict[str, str] = field(default_factory=dict)
