"""Notification TypedDicts for the agent-to-client event stream.

Design §6 canonical taxonomy
------------------------------
All streaming events from the agent MUST be one of the 9 types listed in
``CANONICAL_DISPLAY_EVENTS``.  Adapters translate provider-specific payloads
into these types; they do NOT invent new types.

L14 synthesis contract (Phase 1 task spec §11)
-----------------------------------------------
For any ``turn/submit`` (or equivalent) response that returns a non-null
``reply`` scalar but does NOT produce a ``result/final`` notification before
the response arrives, wrappers MUST synthesi ze a ``result/final`` event
before closing the iterable.  The synthesized event MUST:

* have ``text`` extracted from the ``reply`` scalar,
* have ``turnId`` matched to the in-flight turn, and
* omit ``usage`` (the field is ``NotRequired``).

This guarantees that consumers of the notification stream always see a
``result/final`` to close the turn, whether or not the underlying provider
emitted one explicitly.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

CANONICAL_DISPLAY_EVENTS: tuple[str, ...] = (
    "result/delta",
    "result/final",
    "tool/started",
    "tool/completed",
    "progress",
    "thinking/delta",
    "thinking/final",
    "usage",
    "error",
)
"""The fixed taxonomy. Adapters translate; they do NOT invent new types."""


# ---------------------------------------------------------------------------
# Core streaming notifications
# ---------------------------------------------------------------------------


class ResultDeltaNotification(TypedDict):
    """Incremental text chunk from an in-progress turn."""

    sessionId: str
    turnId: str
    text: str


class ResultFinalNotification(TypedDict):
    """Final text for a completed turn.  ``usage`` may be omitted (e.g. L14 synthesis)."""

    sessionId: str
    turnId: str
    text: str
    usage: NotRequired[dict]


class ToolStartedNotification(TypedDict):
    """Emitted when a tool call begins execution."""

    sessionId: str
    turnId: str
    toolCallId: str
    name: str
    args: dict


class ToolCompletedNotification(TypedDict):
    """Emitted when a tool call finishes execution."""

    sessionId: str
    turnId: str
    toolCallId: str
    name: str
    result: Any
    durationMs: int


class ThinkingDeltaNotification(TypedDict):
    """Incremental thinking/reasoning chunk (extended thinking models)."""

    sessionId: str
    turnId: str
    text: str


class ThinkingFinalNotification(TypedDict):
    """Final thinking/reasoning content for a turn."""

    sessionId: str
    turnId: str
    text: str


class ProgressNotification(TypedDict):
    """Arbitrary progress update.  ``percent`` is optional (0-100)."""

    sessionId: str
    turnId: str
    message: str
    percent: NotRequired[float]


class UsageNotification(TypedDict):
    """Token usage and optional cost summary for a turn."""

    sessionId: str
    turnId: str
    inputTokens: int
    outputTokens: int
    cost: NotRequired[float]


class ErrorNotification(TypedDict):
    """Error event.  ``turnId`` is optional for session-level errors."""

    sessionId: str
    turnId: NotRequired[str]
    code: str
    message: str
    recoverable: bool


# ---------------------------------------------------------------------------
# Approval notifications
# ---------------------------------------------------------------------------


class ApprovalRequestNotification(TypedDict):
    """Requests human (or automated) approval before proceeding."""

    sessionId: str
    turnId: str
    approvalId: str
    kind: str
    payload: dict
    timeoutMs: int


class ApprovalTimeoutNotification(TypedDict):
    """Emitted when an approval request exceeds its timeout."""

    sessionId: str
    turnId: str
    approvalId: str
    kind: str
