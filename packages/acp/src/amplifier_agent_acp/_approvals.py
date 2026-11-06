"""One session's approval handler: the host's static policy, the session's approvals mode, the user's remembered
rules, then the client (section 7)."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
import re
from typing import Literal

from acp.schema import DeniedOutcome, PermissionOption, RequestPermissionResponse
from amplifier_agent import ApprovalRequest, ApprovalResponse, ToolCall
from amplifier_agent._faces import FILE_TOOLS, snapshot, targets

from amplifier_agent_acp._updates import Update, View

Ask = Callable[[str, list[PermissionOption]], Awaitable[RequestPermissionResponse]]
Send = Callable[[Update], Awaitable[None]]

DENIED = "The user denied this call."

Mode = Literal["ask", "allow-reads", "allow-all"]
MODES: tuple[Mode, ...] = ("ask", "allow-reads", "allow-all")


def _target(name: object, working_directory: Path) -> Path | None:
    """The path a read tool resolves `name` to, symlinks resolved, or None for anything but a plain path."""
    # An @mention resolves through the tool's own bundle lookup, which a face cannot repeat.
    if not isinstance(name, str) or not name or name.startswith("@"):
        return None
    path = Path(name).expanduser()
    try:
        return (path if path.is_absolute() else working_directory / path).resolve()
    except (OSError, RuntimeError):
        return None


def _below(pattern: object) -> bool:
    """Whether a glob pattern only matches below its base path: relative, with no `..` component."""
    if not isinstance(pattern, str) or not pattern:
        return False
    return not Path(pattern).is_absolute() and ".." not in re.split(r"[\\/]", pattern)


def reads_inside(call: ToolCall, working_directory: Path, roots: list[Path]) -> bool:
    """Whether a read_file, glob, or grep call reads only inside `roots`, resolved as the tool resolves its paths."""
    arguments = call.arguments
    if call.name == "read_file":
        names = [arguments.get("file_path")]
    elif call.name == "glob":
        if not _below(arguments.get("pattern")):
            return False
        names = [arguments.get("path", ".")]
    elif call.name == "grep":
        if "glob" in arguments and not _below(arguments["glob"]):
            return False
        names = [arguments.get("path", ".")]
    else:
        return False
    for name in names:
        target = _target(name, working_directory)
        if target is None or not any(target.is_relative_to(root) for root in roots):
            return False
    return True


def _rule(request: ApprovalRequest) -> tuple[str, str] | None:
    """What an always-allow answer remembers: the tool, and for bash the exact command."""
    if request.name is None:
        return None
    if request.name != "bash":
        return request.name, ""
    command = (request.arguments or {}).get("command")
    return (request.name, command) if isinstance(command, str) and command else None


def _scope(rule: tuple[str, str]) -> str:
    name, command = rule
    if not command:
        return name
    lines = command.strip().splitlines()
    return f"the command `{lines[0]}`" if len(lines) == 1 else "this exact command"


class Approvals:
    def __init__(
        self, policy: Literal["allow", "deny"] | None, view: View, send: Send, ask: Ask, directories: list[Path]
    ) -> None:
        # The host's static policy, which the client is never asked to override.
        self.policy = policy
        # The session's approvals mode, which exists only when the host sets no policy.
        self.mode: Mode | None = None if policy is not None else "allow-reads"
        # The session's working directory and additional directories, symlinks resolved.
        self.roots = [directory.resolve() for directory in directories]
        self.view = view
        self.send = send
        self.ask = ask
        self.rules: set[tuple[str, str]] = set()
        self.open: dict[str, asyncio.Future[ApprovalResponse]] = {}

    async def __call__(self, request: ApprovalRequest) -> ApprovalResponse:
        if self.policy == "deny":
            return ApprovalResponse("deny")
        # Open from the start, so session/cancel settles the request at every step below.
        future: asyncio.Future[ApprovalResponse] = asyncio.get_running_loop().create_future()
        self.open[request.request_id] = future
        try:
            answer = await self._answer(request, future)
        finally:
            self.open.pop(request.request_id, None)
        view = self.view
        if answer.decision == "allow" and request.call_id is not None and request.name in FILE_TOOLS:
            call = view.sightings.get(request.call_id)
            paths = [] if call is None else targets(call, view.working_directory)
            if paths:
                view.snapshots[request.call_id] = snapshot(paths)
        return answer

    async def _answer(self, request: ApprovalRequest, future: asyncio.Future[ApprovalResponse]) -> ApprovalResponse:
        view = self.view
        if request.call_id is None:
            await self.send(view.request_call(request))
            tool_call_id = request.request_id
        else:
            # A permission request for a tool call the client has not been shown breaks strict clients.
            shown = asyncio.ensure_future(view.sightings.wait(request.call_id))
            try:
                await asyncio.wait({shown, future}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                shown.cancel()
            tool_call_id = request.call_id
        if future.done():
            return future.result()
        rule = _rule(request)
        if self._allowed(request) or (rule is not None and rule in self.rules):
            return ApprovalResponse("allow")
        options = [PermissionOption(option_id="allow_once", name="Allow", kind="allow_once")]
        if rule is not None:
            options.append(
                PermissionOption(
                    option_id="allow_always", name=f"Always allow {_scope(rule)} in this session", kind="allow_always"
                )
            )
        options.append(PermissionOption(option_id="reject_once", name="Deny", kind="reject_once"))
        asking = asyncio.ensure_future(self.ask(tool_call_id, options))

        def answered(task: asyncio.Future[RequestPermissionResponse]) -> None:
            failed = task.cancelled() or task.exception() is not None
            if future.done():
                return
            if failed:
                future.set_result(ApprovalResponse("cancel"))
            else:
                future.set_result(self._decide(task.result(), rule))

        asking.add_done_callback(answered)
        try:
            return await future
        finally:
            if not asking.done():
                asking.cancel()

    def _allowed(self, request: ApprovalRequest) -> bool:
        """Whether the host's policy or the session's approvals mode allows the request without asking."""
        if self.policy == "allow" or self.mode == "allow-all":
            return True
        if self.mode != "allow-reads" or request.call_id is None:
            return False
        call = self.view.sightings.get(request.call_id)
        if call is None or call.source != "built-in":
            return False
        return call.name == "load_skill" or reads_inside(call, self.view.working_directory, self.roots)

    def _decide(self, response: RequestPermissionResponse, rule: tuple[str, str] | None) -> ApprovalResponse:
        outcome = response.outcome
        if isinstance(outcome, DeniedOutcome):
            return ApprovalResponse("cancel")
        if outcome.option_id == "allow_once":
            return ApprovalResponse("allow")
        if outcome.option_id == "allow_always" and rule is not None:
            self.rules.add(rule)
            return ApprovalResponse("allow")
        if outcome.option_id == "reject_once":
            return ApprovalResponse("deny", DENIED)
        # An answer this face did not offer is never read as allow.
        return ApprovalResponse("deny")

    def cancel_open(self) -> None:
        """Settle every open request as cancel, without waiting for the client."""
        for future in list(self.open.values()):
            if not future.done():
                future.set_result(ApprovalResponse("cancel"))
