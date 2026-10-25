"""Turn events, approvals by request, and file changes for the turns endpoint (http-turns.v1)."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
import copy
import dataclasses
from datetime import datetime
from decimal import Decimal
import difflib
import json
import os
from pathlib import Path
from typing import Any, Literal

from amplifier_agent import AgentError, ApprovalRequest, ApprovalResponse, Event, ToolCall

FILE_CHANGES = "com.microsoft.amplifier.file_changes"
# Built-in tools whose effect is writing the files their arguments name.
FILE_TOOLS = ("write_file", "edit_file", "apply_patch")
# apply_patch's V4A markers that name a file the patch adds, updates, deletes, or moves to.
_PATCH_MARKERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")

Policy = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]] | Literal["allow", "deny"]
# A file's bytes before or after a call: None when it does not exist, False when it cannot be read.
Snapshot = list[tuple[Path, bytes | Literal[False] | None]]


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


def targets(call: ToolCall, working_directory: Path) -> list[Path]:
    """The files a built-in file tool's arguments name, resolved as the tool resolves them."""
    if call.source != "built-in" or call.name not in FILE_TOOLS:
        return []
    names: list[str] = []
    if call.name == "apply_patch":
        patch = call.arguments.get("patch")
        if isinstance(patch, str):
            for line in patch.split("\n"):
                line = line.rstrip()
                names.extend(line[len(marker) :] for marker in _PATCH_MARKERS if line.startswith(marker))
    else:
        name = call.arguments.get("file_path")
        # An @mention path resolves through the tool's own bundle lookup, which this face cannot repeat.
        if isinstance(name, str) and name and not name.startswith("@"):
            names.append(name)
    paths: list[Path] = []
    for name in names:
        path = Path(name) if call.name == "apply_patch" else Path(name).expanduser()
        path = Path(os.path.normpath(path if path.is_absolute() else working_directory / path))
        if path not in paths:
            paths.append(path)
    return paths


def _read(path: Path) -> bytes | Literal[False] | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return False


def _text(data: bytes | None) -> str | None:
    if data is None:
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def snapshot(paths: list[Path]) -> Snapshot:
    return [(path, _read(path)) for path in paths]


def file_changes(before: Snapshot, working_directory: Path) -> list[dict[str, Any]]:
    """One entry per text file whose content differs from `before`, in the order of `before`."""
    changes = []
    for path, old in before:
        new = _read(path)
        if old is False or new is False or old == new:
            continue
        old_text, new_text = _text(old), _text(new)
        if old_text is None or new_text is None:
            continue
        try:
            shown = path.relative_to(working_directory).as_posix()
        except ValueError:
            shown = str(path)
        lines = []
        for line in difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            "/dev/null" if old is None else shown,
            "/dev/null" if new is None else shown,
        ):
            lines.append(line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n")
        body = lines[2:]
        changes.append(
            {
                "path": shown,
                "change": "added" if old is None else "deleted" if new is None else "modified",
                "patch": "".join(lines),
                "additions": sum(line.startswith("+") for line in body),
                "deletions": sum(line.startswith("-") for line in body),
            }
        )
    return changes


class TurnScope:
    """One turns request: whether it asks, the tool calls its stream showed, and its open approvals."""

    def __init__(self, asks: bool, working_directory: Path) -> None:
        self.asks = asks
        self.working_directory = Path(os.path.normpath(working_directory))
        self.open: dict[str, asyncio.Future[ApprovalResponse]] = {}
        self._calls: dict[str, ToolCall] = {}
        self._seen: dict[str, asyncio.Event] = {}
        self._snapshots: dict[str, Snapshot] = {}

    def _signal(self, call_id: str) -> asyncio.Event:
        return self._seen.setdefault(call_id, asyncio.Event())

    def observe(self, event: Event) -> None:
        """Record a tool call, or add the file changes of a tool result to its payload."""
        if event.type == "tool_call":
            call = event.payload.call
            self._calls[call.call_id] = call
            self._signal(call.call_id).set()
        elif event.type == "tool_result":
            before = self._snapshots.pop(event.payload.resolution.call_id, None)
            changes = file_changes(before, self.working_directory) if before else []
            if changes:
                vars(event.payload)[FILE_CHANGES] = changes

    async def record_files(self, call_id: str) -> None:
        """Snapshot the files the allowed call is about to change, once its stream has shown the call."""
        # The engine emits tool_call before it asks, so the stream reader sees the call while this waits.
        await self._signal(call_id).wait()
        paths = targets(self._calls[call_id], self.working_directory)
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
