"""Shared wire types for the Amplifier agent protocol.

Re-exports the canonical TypedDict definitions from ``amplifier_agent_lib``
without modification.  No codegen, no drift risk: these are the same objects
as the upstream library — identity (``is``) checks pass.

Consumers should import from this module rather than directly from
``amplifier_agent_lib`` so that the client wrapper can act as a stable
facade over internal library structure changes.
"""

from __future__ import annotations

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

__all__ = [
    "CANONICAL_DISPLAY_EVENTS",
    "PROTOCOL_VERSION",
    "AaaError",
    "AgentShutdownParams",
    "AgentShutdownResult",
    "ApprovalRequestNotification",
    "ApprovalTimeoutNotification",
    "ClientInfo",
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
