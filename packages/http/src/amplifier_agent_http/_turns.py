"""Turn events, approvals by request, and file changes for the turns endpoint (http-turns.v1)."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
import copy
import dataclasses
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any, Literal

from amplifier_agent import AgentError, ApprovalRequest, ApprovalResponse, Event
from amplifier_agent._faces import FILE_TOOLS, CallSightings, Snapshot, file_changes, snapshot, targets

FILE_CHANGES = "com.microsoft.amplifier.file_changes"

Policy = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]] | Literal["allow", "deny"]


def wire(value: Any) -> Any:
    """The JSON form of a binding value: times as ISO 8601, decimals as strings, extension fields kept."""
    if isinstance(value, AgentError):
        return {key: wire(item) for key, item in vars(value).items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: wire(item) for key, item in vars(value).items() if not key.startswith("_")}
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    return value


def event_line(event: Event) -> bytes:
    return f"data: {json.dumps(wire(event), allow_nan=False, separators=(',', ':'))}\n\n".encode()


class TurnScope:
    """One turns request: whether it asks, the tool calls its stream showed, and its open approvals."""

    def __init__(self, asks: bool, working_directory: Path) -> None:
        self.asks = asks
        self.working_directory = Path(os.path.normpath(working_directory))
        self.open: dict[str, asyncio.Future[ApprovalResponse]] = {}
        self._sightings = CallSightings()
        self._snapshots: dict[str, Snapshot] = {}

    def observe(self, event: Event) -> None:
        """Record a tool call, or add the file changes of a tool result to its payload."""
        if event.type == "tool_call":
            self._sightings.see(event.payload.call)
        elif event.type == "tool_result":
            before = self._snapshots.pop(event.payload.resolution.call_id, None)
            changes = file_changes(before, self.working_directory) if before else []
            if changes:
                vars(event.payload)[FILE_CHANGES] = changes

    async def record_files(self, call_id: str) -> None:
        """Snapshot the files the allowed call is about to change, once its stream has shown the call."""
        paths = targets(await self._sightings.wait(call_id), self.working_directory)
        if paths:
            self._snapshots[call_id] = snapshot(paths)

    def cancel_open(self) -> None:
        for future in self.open.values():
            if not future.done():
                future.set_result(ApprovalResponse("cancel"))


# The turns request whose turn is running; a turn's tasks inherit it from the request that started the turn.
current_scope: ContextVar[TurnScope | None] = ContextVar("current_scope", default=None)


class Approvals:
    """The face's approval handler: the server's policy decides, and a request that asks narrows it."""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.pending: dict[str, asyncio.Future[ApprovalResponse]] = {}

    async def __call__(self, request: ApprovalRequest) -> ApprovalResponse:
        if callable(self.policy):
            answer = await self.policy(copy.deepcopy(request))
        else:
            answer = ApprovalResponse(self.policy)
        scope = current_scope.get()
        if scope is None or answer.decision != "allow":
            return answer
        if scope.asks:
            answer = await self._ask(request.request_id, scope)
        if answer.decision == "allow" and request.call_id is not None and request.name in FILE_TOOLS:
            await scope.record_files(request.call_id)
        return answer

    async def _ask(self, request_id: str, scope: TurnScope) -> ApprovalResponse:
        future: asyncio.Future[ApprovalResponse] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = scope.open[request_id] = future
        try:
            return await future
        finally:
            self.pending.pop(request_id, None)
            scope.open.pop(request_id, None)

    def answer(self, request_id: str, response: ApprovalResponse) -> bool:
        """Settle an open request; False when it is unknown, settled, or expired."""
        future = self.pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(response)
        return True
