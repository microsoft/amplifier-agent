"""One session's approval handler: the host's ceiling, the user's remembered rules, then the client (section 7)."""

import asyncio
from collections.abc import Awaitable, Callable

from acp.schema import DeniedOutcome, PermissionOption, RequestPermissionResponse
from amplifier_agent import ApprovalRequest, ApprovalResponse
from amplifier_agent._faces import FILE_TOOLS, snapshot, targets

from amplifier_agent_acp._updates import Update, View

Ask = Callable[[str, list[PermissionOption]], Awaitable[RequestPermissionResponse]]
Send = Callable[[Update], Awaitable[None]]

DENIED = "The user denied this call."


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
    def __init__(self, deny_all: bool, view: View, send: Send, ask: Ask) -> None:
        self.deny_all = deny_all
        self.view = view
        self.send = send
        self.ask = ask
        self.rules: set[tuple[str, str]] = set()
        self.open: dict[str, asyncio.Future[ApprovalResponse]] = {}

    async def __call__(self, request: ApprovalRequest) -> ApprovalResponse:
        if self.deny_all:
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
        if rule is not None and rule in self.rules:
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
