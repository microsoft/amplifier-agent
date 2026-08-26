"""Public dataclass types for the Amplifier Agent Python wrapper.

DisplayEvent variants, EngineInfo, McpServerConfig, and helpers that the
public API exposes.  Mirrors the TypeScript wrapper's `DisplayEvent` discriminated
union and the schema-generated wire types.

DisplayEvent is a tagged union of frozen dataclasses; consumers should switch on
the ``type`` field literal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .errors import Classification, Severity

# ---------------------------------------------------------------------------
# Usage (mirror `Usage` in wrappers/typescript/src/session.ts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Usage:
    """Per-turn token and cost accounting, as reported by the engine.

    Read straight off the §4.1 envelope's ``metadata`` block.  The wrapper does
    NOT sum anything: the engine's ``UsageAccumulator`` already folded every
    ``usage`` display event of the turn (including sub-agent LLM calls) into
    these totals, so re-summing wrapper-side would double-count.

    Attributes
    ----------
    input_tokens:
        Input tokens **charged**, mirroring the envelope's ``tokensIn``.  This
        is new input + cache reads + cache writes; the model saw all three as
        input and the split is a billing distinction.  A host that wants the
        new-only figure derives it as
        ``input_tokens - cache_read_tokens - cache_write_tokens``.
    output_tokens:
        Output tokens (envelope ``tokensOut``).
    cache_read_tokens:
        Input tokens served from the provider's prompt cache.
    cache_write_tokens:
        Input tokens written into the provider's prompt cache.
    cost_usd:
        Turn cost as a ``Decimal``, parsed from the envelope's decimal
        ``costUsd`` STRING.  Never a float -- binary floats accumulate drift the
        moment a host sums them.  ``None`` when no provider reported a cost;
        that is not the same claim as ``Decimal("0")``.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal | None = None


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
    """Yielded once when the subprocess emits a successful §4.1 envelope.

    ``session_id``, ``turn_id`` and ``exit_code`` are never optional here: a
    ``ResultEvent`` exists only on the envelope path, so the identity fields the
    envelope carries are always known.  (``ErrorEvent`` can be synthesized with
    no envelope at all, which is why its equivalents are nullable.)

    ``usage`` is ``None`` only when the envelope's ``metadata`` carried no usage
    keys whatsoever -- an engine older than protocol 0.4.0.  ``None`` means "not
    reported", which is a different claim from a populated ``Usage`` reading
    zero (a turn that made no LLM call really did spend nothing).

    ``stderr_tail`` holds the last ``stderr_tail_bytes`` BYTES of the engine's
    stderr, or the whole buffer when that option is ``None``, or ``None`` when
    it is ``0`` or stderr was empty.
    """

    text: str
    session_id: str
    turn_id: str
    exit_code: int
    usage: Usage | None = None
    stderr_tail: str | None = None
    type: Literal["result"] = "result"


@dataclass(frozen=True, kw_only=True)
class ErrorEvent:
    """Yielded once when the subprocess errors, hangs, or fails to spawn.

    ``session_id`` / ``turn_id`` / ``exit_code`` / ``usage`` are populated from
    the §4.1 envelope when one was parsed.

    On the synthesized (Rule 2) paths -- envelope absent, unparseable, spawn
    failure, or hang -- there is no envelope to read them from, and the fields
    split by who knows the answer:

    * ``session_id`` IS populated whenever the wrapper itself knows it, which
      is every failure raised through a ``SessionHandle``: the handle was given
      the session id at construction time, so a host correlating the failure
      gets the same identifier it passed in.  It is ``None`` only when
      ``parse_run_output`` is called directly without a
      ``fallback_session_id``.
    * ``turn_id`` is ``None``.  The engine assigns turn ids and no envelope came
      back, so the wrapper genuinely does not know it and will not invent one.
    * ``exit_code`` is present on the parser's Rule 2 paths (the process did
      exit) and ``None`` on spawn failure and hang, where it never did.
    * ``usage`` is ``None``: only the envelope reports it.

    ``usage`` on the failure path is not a duplicate report: nothing else on the
    failure path carries usage, so a turn that burned tokens and then failed
    would otherwise spend them invisibly.
    """

    code: str
    classification: Classification
    severity: Severity
    correlation_id: str
    message: str
    retryable: bool
    stderr_tail: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    exit_code: int | None = None
    usage: Usage | None = None
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
