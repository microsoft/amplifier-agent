"""Protocol-point abstractions injected into Engine.boot().

Two protocol points are externally exposed (design §6):

  ApprovalSystem — interactive, request/response;
  DisplaySystem  — one-way, event stream (folds in streaming).

Spawn is NOT a protocol point. It is library-internal per design §8 / Brian D3.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable


class DisplayEvent(TypedDict):
    """A single display event emitted by the engine.

    ``type`` is one of ``CANONICAL_DISPLAY_EVENTS``; payload keys vary by type.
    """

    type: str
    sessionId: str
    turnId: NotRequired[str]


@runtime_checkable
class DisplaySystem(Protocol):
    """One-way display event sink injected at ``Engine.boot()``.

    ``emit`` is intentionally synchronous; the stdio bridge queues internally.
    """

    def emit(self, event: DisplayEvent) -> None:
        """Emit a display event."""
        ...


ApprovalAction = Literal["accept", "decline", "cancel"]


class ApprovalRequest(TypedDict):
    """Parameters for an interactive approval request."""

    sessionId: str
    turnId: str
    approvalId: str
    kind: str
    payload: dict[str, Any]
    timeoutMs: int


class ApprovalResponse(TypedDict):
    """Response returned by an ApprovalSystem implementation."""

    action: ApprovalAction
    payload: NotRequired[dict[str, Any]]


@runtime_checkable
class ApprovalSystem(Protocol):
    """Interactive approval protocol point injected at ``Engine.boot()``.

    Implementations MUST honor ``timeoutMs`` and return ``{'action': 'cancel'}``
    on timeout.
    """

    async def request(self, req: ApprovalRequest) -> ApprovalResponse:
        """Submit an approval request and await the response."""
        ...


class ProtocolPoints(TypedDict):
    """Container for all protocol points injected into ``Engine.boot()``."""

    approval: ApprovalSystem
    display: DisplaySystem
