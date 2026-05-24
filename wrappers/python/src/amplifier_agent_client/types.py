"""Shared wire types for the Amplifier agent protocol.

Re-exports the canonical TypedDict definitions from ``amplifier_agent_lib``
without modification, and adds the Mode A v2 (A3'/CR-C) ``DisplayEvent``
discriminated union — a Literal-discriminated TypedDict union that mirrors
the TypeScript ``DisplayEvent`` type from wrappers/typescript/src/session.ts.

DisplayEvent variants (amendment §5.2):
  - ``{"type": "init",     "sessionId": str}``
  - ``{"type": "activity"}``
  - ``{"type": "result",   "text": str}``
  - ``{"type": "error",    "code": str,
                           "classification": Literal[...],
                           "severity": Literal[...],
                           "correlationId": str,
                           "message": str,
                           "stderrTail": str (optional),
                           "retryable": bool}``
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.errors import AaaError, ErrorCode

# ---------------------------------------------------------------------------
# Method params / results + shared types
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.methods import (
    PROTOCOL_VERSION,
    AgentShutdownParams,
    AgentShutdownResult,
    ClientInfo,
    InitializeParams,
    InitializeResult,
    ServerInfo,
    SessionState,
    TurnSubmitParams,
    TurnSubmitResult,
)

# ---------------------------------------------------------------------------
# Notification types + event taxonomy
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.notifications import (
    CANONICAL_DISPLAY_EVENTS,
    ApprovalRequestNotification,
    ApprovalTimeoutNotification,
    ErrorNotification,
    ProgressNotification,
    ResultDeltaNotification,
    ResultFinalNotification,
    ToolCompletedNotification,
    ToolStartedNotification,
)

# ---------------------------------------------------------------------------
# Mode A v2 DisplayEvent — discriminated union per amendment §5.2 (CR-C)
# ---------------------------------------------------------------------------

#: Allowed values for ``DisplayEventError["classification"]``.
DisplayEventClassification = Literal["transport", "protocol", "engine", "approval", "unknown"]

#: Allowed values for ``DisplayEventError["severity"]``.
DisplayEventSeverity = Literal["error", "warning"]


class DisplayEventInit(TypedDict):
    """``{"type": "init", "sessionId": str}`` — yielded synchronously before subprocess spawn."""

    type: Literal["init"]
    sessionId: str


class DisplayEventActivity(TypedDict):
    """``{"type": "activity"}`` — heartbeat tick every 2s while subprocess is alive."""

    type: Literal["activity"]


class DisplayEventResult(TypedDict):
    """``{"type": "result", "text": str}`` — terminal success event."""

    type: Literal["result"]
    text: str


class DisplayEventError(TypedDict):
    """``{"type": "error", ...}`` — terminal failure event.

    Fields per amendment §5.2:
      - ``code``           — error code string (e.g. "engine_exit_1").
      - ``classification`` — one of transport/protocol/engine/approval/unknown.
      - ``severity``       — "error" or "warning".
      - ``correlationId``  — string; empty string when unavailable.
      - ``message``        — human-readable error description.
      - ``stderrTail``     — optional last 4KB of subprocess stderr.
      - ``retryable``      — always False in v1 (no retry plan in Mode A).
    """

    type: Literal["error"]
    code: str
    classification: DisplayEventClassification
    severity: DisplayEventSeverity
    correlationId: str
    message: str
    stderrTail: NotRequired[str]
    retryable: bool


#: Mode A v2 DisplayEvent — a discriminated union of four variants.
DisplayEvent = DisplayEventInit | DisplayEventActivity | DisplayEventResult | DisplayEventError


__all__ = [
    "CANONICAL_DISPLAY_EVENTS",
    "PROTOCOL_VERSION",
    "AaaError",
    "AgentShutdownParams",
    "AgentShutdownResult",
    "ApprovalRequestNotification",
    "ApprovalTimeoutNotification",
    "ClientInfo",
    "DisplayEvent",
    "DisplayEventActivity",
    "DisplayEventClassification",
    "DisplayEventError",
    "DisplayEventInit",
    "DisplayEventResult",
    "DisplayEventSeverity",
    "ErrorCode",
    "ErrorNotification",
    "InitializeParams",
    "InitializeResult",
    "ProgressNotification",
    "ResultDeltaNotification",
    "ResultFinalNotification",
    "ServerInfo",
    "SessionState",
    "ToolCompletedNotification",
    "ToolStartedNotification",
    "TurnSubmitParams",
    "TurnSubmitResult",
]
