"""Public Python values for agent operations and observations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal


class AgentError(Exception):
    def __init__(
        self,
        code: str,
        category: str,
        message: str,
        remedy: str,
        retryable: bool = False,
        correlation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.remedy = remedy
        self.retryable = retryable
        self.correlation_id = correlation_id
        self.details = details

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            type(self),
            (
                self.code,
                self.category,
                self.message,
                self.remedy,
                self.retryable,
                self.correlation_id,
                self.details,
            ),
            vars(self),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AgentError):
            return NotImplemented
        return vars(self) == vars(other)


class ToolFailed(Exception):
    """The caller executor authoritatively reports a failed effect."""


class ToolOutcomeUnknown(Exception):
    """The caller executor cannot establish whether its effect completed."""


@dataclass
class TextPart:
    text: str
    type: Literal["text"] = "text"


ContentPart = TextPart


@dataclass
class ConversationMessage:
    role: Literal["system", "developer", "user", "assistant"]
    content: list[ContentPart]


@dataclass
class TurnInput:
    content: list[ContentPart]
    model: str | None = None
    history: list[ConversationMessage] | None = None


@dataclass
class UsageEntry:
    provider: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost: dict[str, Decimal] | None = None


@dataclass
class Usage:
    entries: list[UsageEntry]


@dataclass
class TurnResult:
    state: Literal["success", "failure", "rejected", "cancelled"]
    content: list[ContentPart] | None = None
    error: AgentError | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    persistence: Literal["durable", "ephemeral"]


@dataclass(frozen=True)
class TurnInfo:
    session_id: str
    turn_id: str


@dataclass
class TurnRecord:
    turn_id: str
    input: TurnInput
    result: TurnResult


@dataclass
class Event:
    contract_version: str
    session_id: str
    turn_id: str
    sequence: int
    type: str
    payload: Any
    at: datetime | None = None


@dataclass(frozen=True)
class ToolContext:
    call_id: str
    deadline: datetime | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    safety: dict[str, Any] | None = None


@dataclass
class ToolCall:
    call_id: str
    name: str
    source: Literal["built-in", "caller", "mcp"]
    arguments: dict[str, Any]
    deadline: datetime | None = None


@dataclass
class ToolResolution:
    call_id: str
    outcome: Literal["completed", "failed", "cancelled", "unknown"]
    content: str | None = None
    error: AgentError | None = None


@dataclass
class McpServer:
    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


@dataclass
class ApprovalRequest:
    request_id: str
    summary: str
    call_id: str | None = None
    name: str | None = None


@dataclass
class ApprovalResponse:
    decision: Literal["allow", "deny", "cancel"]
    reason: str | None = None


ApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]


@dataclass
class AgentOptions:
    provider: str | None = None
    model: str | None = None
    instructions: str | None = None
    tools: list[Tool] | None = None
    skills: list[str] | None = None
    mcp_servers: list[McpServer] | None = None
    storage: str | Path | None = None
    approvals: ApprovalHandler | Literal["allow", "deny"] | None = None
    tool_error_policy: Literal["stop", "continue"] = "stop"


@dataclass
class SessionOptions:
    session_id: str | None = None
    persistence: Literal["durable", "ephemeral"] = "durable"
    model: str | None = None


@dataclass
class Selection:
    provider: str
    model: str


@dataclass
class TurnStarted:
    continuation: Literal["fresh", "resumed"]
    primary_actual: Selection


@dataclass
class OutputDelta:
    content: list[ContentPart]


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ReasoningFinal:
    text: str


@dataclass
class ToolCallEvent:
    call: ToolCall


@dataclass
class ToolResultEvent:
    resolution: ToolResolution


@dataclass
class ApprovalRequestEvent:
    request: ApprovalRequest


@dataclass
class ApprovalResolution:
    request_id: str
    decision: str
    reason: str | None = None


@dataclass
class ApprovalDecision:
    resolution: ApprovalResolution


@dataclass
class Progress:
    data: Any


@dataclass
class UsageEvent:
    snapshot: Usage
