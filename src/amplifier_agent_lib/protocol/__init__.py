"""Protocol sub-package for amplifier_agent_lib.

Public API — everything a consumer needs is importable directly from
``amplifier_agent_lib.protocol``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.capabilities import (
    ApprovalCapability,
    ClientCapabilities,
    DisplayCapability,
    ServerCapabilities,
    negotiate_capabilities,
    server_default_capabilities,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.errors import AaaError, ErrorCode

# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.methods import (
    PROTOCOL_VERSION,
    AgentShutdownParams,
    AgentShutdownResult,
    CacheInfoParams,
    CacheInfoResult,
    ClientInfo,
    InitializeParams,
    InitializeResult,
    ServerInfo,
    SessionCreateParams,
    SessionCreateResult,
    SessionEndParams,
    SessionEndResult,
    SessionState,
    TurnCancelParams,
    TurnCancelResult,
    TurnSubmitParams,
    TurnSubmitResult,
)

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
from amplifier_agent_lib.protocol.notifications import (
    CANONICAL_DISPLAY_EVENTS,
    ApprovalRequestNotification,
    ApprovalTimeoutNotification,
    ErrorNotification,
    ProgressNotification,
    ResultDeltaNotification,
    ResultFinalNotification,
    ThinkingDeltaNotification,
    ThinkingFinalNotification,
    ToolCompletedNotification,
    ToolStartedNotification,
    UsageNotification,
)

__all__ = [
    "CANONICAL_DISPLAY_EVENTS",
    "PROTOCOL_VERSION",
    "AaaError",
    "AgentShutdownParams",
    "AgentShutdownResult",
    "ApprovalCapability",
    "ApprovalRequestNotification",
    "ApprovalTimeoutNotification",
    "CacheInfoParams",
    "CacheInfoResult",
    "ClientCapabilities",
    "ClientInfo",
    "DisplayCapability",
    "ErrorCode",
    "ErrorNotification",
    "InitializeParams",
    "InitializeResult",
    "ProgressNotification",
    "ResultDeltaNotification",
    "ResultFinalNotification",
    "ServerCapabilities",
    "ServerInfo",
    "SessionCreateParams",
    "SessionCreateResult",
    "SessionEndParams",
    "SessionEndResult",
    "SessionState",
    "ThinkingDeltaNotification",
    "ThinkingFinalNotification",
    "ToolCompletedNotification",
    "ToolStartedNotification",
    "TurnCancelParams",
    "TurnCancelResult",
    "TurnSubmitParams",
    "TurnSubmitResult",
    "UsageNotification",
    "negotiate_capabilities",
    "server_default_capabilities",
]
