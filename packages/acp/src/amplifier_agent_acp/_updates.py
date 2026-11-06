"""Turn events as ACP session updates (acp-face.v1 sections 5, 6, and 8)."""

from decimal import Decimal
import os
from pathlib import Path

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ConfigOptionUpdate,
    Cost,
    ImageContentBlock,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    UserMessageChunk,
)
from amplifier_agent import ApprovalRequest, Event, ImagePart, OutputDelta, ToolCall, TurnRecord, Usage
from amplifier_agent._faces import CallSightings, Snapshot, text_changes

from amplifier_agent_acp import _tools

Update = (
    AgentMessageChunk
    | AgentThoughtChunk
    | ConfigOptionUpdate
    | ToolCallProgress
    | ToolCallStart
    | UsageUpdate
    | UserMessageChunk
)


def usd(usage: Usage | None) -> Decimal | None:
    """The USD cost of `usage`, or None when an entry's is unknown. No usage is no attributable work."""
    if usage is None:
        return Decimal(0)
    total = Decimal(0)
    for entry in usage.entries:
        if entry.cost is None or "USD" not in entry.cost:
            return None
        total += entry.cost["USD"]
    return total


def _plus(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    return None if left is None or right is None else left + right


class View:
    """What one session's client has been shown: its tool calls, file snapshots, and cost."""

    def __init__(self, working_directory: Path, terminal_output: bool) -> None:
        self.working_directory = Path(os.path.normpath(working_directory))
        self.terminal_output = terminal_output
        # The final cost of the session's earlier turns.
        self.earlier_cost: Decimal | None = Decimal(0)
        self.begin_turn()

    def begin_turn(self) -> None:
        self.sightings = CallSightings()
        self.snapshots: dict[str, Snapshot] = {}
        # The latest usage snapshot's cost.
        self.turn_cost: Decimal | None = Decimal(0)
        self.reasoning_streamed = False
        # Tool calls shown and not yet settled, by toolCallId.
        self.unsettled: dict[str, ToolCall | None] = {}
        # The toolCallId each approval request is about, and the requests without a call that were allowed.
        self.requests: dict[str, str] = {}
        self.allowed_requests: set[str] = set()

    def count_history(self, history: list[TurnRecord]) -> None:
        for record in history:
            self.earlier_cost = _plus(self.earlier_cost, usd(record.result.usage))

    def request_call(self, request: ApprovalRequest) -> ToolCallStart:
        """The tool_call an approval request without a call_id is shown as."""
        self.requests[request.request_id] = request.request_id
        self.unsettled[request.request_id] = None
        return ToolCallStart(
            session_update="tool_call",
            tool_call_id=request.request_id,
            title=request.summary,
            kind="execute",
            status="pending",
            raw_input=request.arguments,
        )

    def _tool_call(self, call: ToolCall) -> ToolCallStart:
        self.unsettled[call.call_id] = call
        content = _tools.preview(call, self.working_directory)
        meta = None
        if self.terminal_output and _tools.is_builtin(call, "bash"):
            content = [TerminalToolCallContent(type="terminal", terminal_id=call.call_id)]
            meta = {"terminal_info": {"terminal_id": call.call_id}}
        return ToolCallStart(
            session_update="tool_call",
            tool_call_id=call.call_id,
            title=_tools.title(call, self.working_directory),
            kind=_tools.kind(call),
            status="pending",
            name=call.name,
            raw_input=call.arguments,
            content=content,
            locations=_tools.locations(call, self.working_directory),
            field_meta=meta,
        )

    def settle(self) -> list[Update]:
        """A final status for every tool call shown without one: an allowed request alone completed, others failed."""
        updates: list[Update] = [
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=tool_call_id,
                status="completed" if call is None and tool_call_id in self.allowed_requests else "failed",
            )
            for tool_call_id, call in self.unsettled.items()
        ]
        self.unsettled.clear()
        return updates

    def updates(self, event: Event) -> list[Update]:
        """The session updates one turn event becomes, live or replayed."""
        payload = event.payload
        match event.type:
            case "output_delta":
                text = "".join(part.text for part in payload.content)
                if not text:
                    return []
                return [
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text=text),
                        message_id=event.turn_id,
                    )
                ]
            case "reasoning_delta" | "reasoning_final":
                # A final repeats the deltas of its block, so only a final without them is new.
                text = payload.text
                if event.type == "reasoning_final":
                    text = "" if self.reasoning_streamed else text
                    self.reasoning_streamed = False
                else:
                    self.reasoning_streamed = True
                if not text:
                    return []
                return [
                    AgentThoughtChunk(
                        session_update="agent_thought_chunk", content=TextContentBlock(type="text", text=text)
                    )
                ]
            case "tool_call":
                return [self._tool_call(payload.call)]
            case "approval_request":
                request = payload.request
                self.requests[request.request_id] = request.call_id or request.request_id
                return []
            case "approval_decision":
                resolution = payload.resolution
                tool_call_id = self.requests.get(resolution.request_id)
                if resolution.decision != "allow" or tool_call_id is None:
                    return []
                if tool_call_id == resolution.request_id:
                    self.allowed_requests.add(tool_call_id)
                return [
                    ToolCallProgress(session_update="tool_call_update", tool_call_id=tool_call_id, status="in_progress")
                ]
            case "tool_result":
                resolution = payload.resolution
                call = self.unsettled.pop(resolution.call_id, None)
                before = self.snapshots.pop(resolution.call_id, None)
                changes = text_changes(before) if before else []
                content, meta = _tools.result_content(call, resolution, changes, self.terminal_output)
                return [
                    ToolCallProgress(
                        session_update="tool_call_update",
                        tool_call_id=resolution.call_id,
                        status="completed" if resolution.outcome == "completed" else "failed",
                        content=content,
                        field_meta=meta,
                    )
                ]
            case "usage":
                self.turn_cost = usd(payload.snapshot)
                return []
            case "progress":
                size = payload.data.get("context_size") if isinstance(payload.data, dict) else None
                tokens = size.get("tokens") if isinstance(size, dict) else None
                budget = size.get("budget") if isinstance(size, dict) else None
                if type(tokens) is not int or type(budget) is not int or tokens < 0 or budget < 0:
                    return []
                cost = _plus(self.earlier_cost, self.turn_cost)
                return [
                    UsageUpdate(
                        session_update="usage_update",
                        used=tokens,
                        size=budget,
                        cost=None if cost is None else Cost(amount=float(cost), currency="USD"),
                    )
                ]
            case "terminal":
                result = payload
                final = usd(result.usage) if result.usage is not None else self.turn_cost
                self.earlier_cost = _plus(self.earlier_cost, final)
                return self.settle()
            case _:
                return []


def replay(view: View, record: TurnRecord, session_id: str) -> list[Update]:
    """One stored turn: its input as user message chunks, then its reply through the live conversion."""
    updates: list[Update] = []
    for part in record.input.content:
        block = (
            ImageContentBlock(type="image", mime_type=part.media_type, data=part.data)
            if isinstance(part, ImagePart)
            else TextContentBlock(type="text", text=part.text)
        )
        updates.append(UserMessageChunk(session_update="user_message_chunk", content=block))
    reply = Event(
        "turn-events/1", session_id, record.turn_id, 0, "output_delta", OutputDelta(record.result.content or [])
    )
    updates.extend(view.updates(reply))
    return updates
