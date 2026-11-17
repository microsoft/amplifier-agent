"""ACP permissions repeat the engine-authored, announced call without interpreting arguments."""

import asyncio
import copy

from acp.schema import AllowedOutcome, McpServerStdio, RequestPermissionResponse, TextContentBlock
from amplifier_agent import ApprovalRequest, Event, ToolCall, ToolCallEvent
from amplifier_agent_acp._approvals import Approvals
from amplifier_agent_acp._updates import View
import pytest

from tests.support.engine import provision

NAMESPACE = "com.microsoft.amplifier.tool"
MCP = {"version": 1, "kind": "mcp", "server": "other_server", "name": "work.report"}


class OfflineMcp:
    def __init__(self, server, environment, directory):
        self.server = copy.deepcopy(server)
        self.client = None
        self.shutdown = asyncio.Event()
        self.tools = [{"name": "work.report", "input_schema": {"type": "object"}}]

    async def connect(self):
        pass

    async def close(self):
        self.shutdown.set()

    async def call(self, name, arguments):
        raise AssertionError("The denied fixture must not invoke MCP")


@pytest.mark.parametrize("source", ["builtin", "mcp"])
async def test_public_face_advertises_and_repeats_exact_registry_identity(connect, monkeypatch, tmp_path, source):
    from amplifier_agent_engine._engine import mcp_tools

    monkeypatch.setattr(mcp_tools, "MCPConnection", OfflineMcp)
    name = "write_file" if source == "builtin" else "mcp_other_server_work_report"
    arguments = (
        {"file_path": "untouched.txt", "content": "not written"}
        if source == "builtin"
        else {
            "server": "minecraft",
            "tool": "observe",
            "_meta": {NAMESPACE: {**MCP, "server": "minecraft"}},
        }
    )
    expected = {"version": 1, "kind": "builtin", "name": name} if source == "builtin" else MCP
    provision(monkeypatch, [{"tool": {"name": name, "arguments": arguments}}, {"text": "Refused"}])
    async with connect("reject_once") as (connection, recorder):
        initialized = await connection.initialize(protocol_version=1)
        opened = await connection.new_session(
            cwd=str(tmp_path), mcp_servers=[McpServerStdio(name="other_server", command="unused", args=[], env=[])]
        )
        await connection.prompt(session_id=opened.session_id, prompt=[TextContentBlock(type="text", text="Check")])
    assert initialized.agent_capabilities.field_meta == {NAMESPACE: {"version": 1, "rawInputShape": "arguments"}}
    announcements = [
        m["params"]["update"]
        for m in recorder.wire
        if m.get("method") == "session/update" and m["params"]["update"]["sessionUpdate"] == "tool_call"
    ]
    permissions = [m["params"]["toolCall"] for m in recorder.wire if m.get("method") == "session/request_permission"]
    assert len(announcements) == len(permissions) == 1
    announced, asked = announcements[0], permissions[0]
    for key in ("toolCallId", "title", "kind", "rawInput"):
        assert asked[key] == announced[key]
    assert asked["rawInput"] == arguments
    assert asked["_meta"][NAMESPACE] == announced["_meta"][NAMESPACE] == expected
    assert not (tmp_path / "untouched.txt").exists()


@pytest.mark.parametrize("changed", ["identity", "server", "name", "arguments", "numeric-type", "missing"])
async def test_mismatched_permission_cannot_borrow_announced_identity(tmp_path, changed):
    view = View(tmp_path, False)
    call = ToolCall("call", "mcp_other_server_work_report", "mcp", {"value": 1})
    setattr(call, NAMESPACE, copy.deepcopy(MCP))
    view.updates(Event("turn-events/1", "session", "turn", 1, "tool_call", ToolCallEvent(call)))
    view.sightings.see(call)
    request = ApprovalRequest("request", "Run", "call", call.name, copy.deepcopy(call.arguments))
    setattr(request, NAMESPACE, copy.deepcopy(MCP))
    if changed == "identity":
        getattr(request, NAMESPACE)["name"] = "observe"
    elif changed == "server":
        getattr(request, NAMESPACE)["server"] = "minecraft"
    elif changed == "name":
        request.name = "another_call"
    elif changed == "arguments":
        request.arguments = {"value": 2}
    elif changed == "numeric-type":
        request.arguments = {"value": True}
    else:
        delattr(request, NAMESPACE)

    asked = []

    async def send(update):
        pass

    async def ask(actual, options):
        asked.append(actual)
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_once"))

    approvals = Approvals(None, view, send, ask, [tmp_path])
    approvals.mode = "ask"
    answer = await approvals(request)
    assert not asked
    assert answer.decision == "cancel"


async def test_matching_permission_preserves_terminal_metadata_and_cancellation(tmp_path):
    view, entered = View(tmp_path, True), asyncio.Event()
    asked = []
    call = ToolCall("call", "bash", "built-in", {"command": "unused"})
    identity = {"version": 1, "kind": "builtin", "name": "bash"}
    setattr(call, NAMESPACE, identity)
    shown = view.updates(Event("turn-events/1", "session", "turn", 1, "tool_call", ToolCallEvent(call)))[0]
    view.sightings.see(call)
    request = ApprovalRequest("request", "Run", "call", "bash", {"command": "unused"})
    setattr(request, NAMESPACE, copy.deepcopy(identity))

    async def send(update):
        pass

    async def ask(actual, options):
        asked.append(actual)
        entered.set()
        await asyncio.Event().wait()
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_once"))

    approvals = Approvals(None, view, send, ask, [tmp_path], instance_owned=True)
    pending = asyncio.create_task(approvals(request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        actual = asked[0]
        assert actual.title == shown.title
        assert actual.raw_input == shown.raw_input
        assert actual.field_meta[NAMESPACE] == identity
        assert shown.field_meta is not None
        assert shown.field_meta["terminal_info"] == {"terminal_id": "call"}
    finally:
        approvals.cancel_open()
        await asyncio.wait_for(pending, 2)
    assert (await asyncio.wait_for(pending, 2)).decision == "cancel"


@pytest.mark.parametrize(
    ("source", "extension"),
    [
        ("caller", MCP),
        ("built-in", MCP),
        ("mcp", {**MCP, "version": True}),
        ("mcp", {**MCP, "version": 2}),
        ("mcp", {**MCP, "server": ""}),
        ("mcp", {**MCP, "unexpected": "claim"}),
        ("mcp", {"version": 1, "kind": "builtin", "name": "read_file"}),
    ],
)
def test_untrusted_or_malformed_identity_is_not_announced(tmp_path, source, extension):
    view = View(tmp_path, False)
    call = ToolCall("call", "mcp_minecraft_observe", source, {"_meta": {NAMESPACE: MCP}})
    setattr(call, NAMESPACE, extension)
    shown = view.updates(Event("turn-events/1", "session", "turn", 1, "tool_call", ToolCallEvent(call)))[0]
    assert NAMESPACE not in (shown.field_meta or {})


async def test_unresolved_legacy_request_keeps_once_and_deny_without_identity(tmp_path):
    view = View(tmp_path, False)
    call = ToolCall("call", "mcp_minecraft_observe", "caller", {"server": "minecraft"})
    view.updates(Event("turn-events/1", "session", "turn", 1, "tool_call", ToolCallEvent(call)))
    view.sightings.see(call)
    asked = []

    async def send(update):
        pass

    async def ask(actual, options):
        asked.append(actual)
        assert NAMESPACE not in (actual.field_meta or {})
        assert [option.option_id for option in options] == ["allow_once", "reject_once"]
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_once"))

    approvals = Approvals(None, view, send, ask, [tmp_path], instance_owned=True)
    request = ApprovalRequest("request", "Run", "call", call.name, call.arguments)
    assert (await approvals(request)).decision == "allow"
    assert len(asked) == 1
    view.begin_turn()
    assert not view.announcements


async def test_request_without_tool_call_keeps_its_original_permission_flow(tmp_path):
    view, shown = View(tmp_path, False), []

    async def send(update):
        shown.append(update)

    async def ask(actual, options):
        assert actual.title == "Host action"
        assert actual.raw_input == {"value": "fixture"}
        assert NAMESPACE not in (actual.field_meta or {})
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="reject_once"))

    approvals = Approvals(None, view, send, ask, [tmp_path])
    request = ApprovalRequest("request", "Host action", name="host", arguments={"value": "fixture"})
    assert (await approvals(request)).decision == "deny"
    assert len(shown) == 1
