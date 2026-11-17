"""Resolved tool identity is independent of model arguments and presentation names."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

from amplifier_agent._binding._engine_adapter import RecordBridge
from amplifier_agent_engine import _records
from amplifier_agent_engine._engine.configuration import ResolvedConfig
from amplifier_agent_engine._engine.effects import PolicyStop, RecoveryState, execute_tool
from amplifier_agent_engine._engine.mcp_tools import MCPConnection, registered_mcp_tool
from amplifier_agent_engine._engine.storage import CommittedTurn
from amplifier_agent_engine._engine.tools import SCHEMA, RegisteredTool
from amplifier_agent_engine._records import McpServer, ToolActivity, TurnInput, TurnRecord, TurnResult
from amplifier_agent_http._turns import wire
import pytest

NAMESPACE = "com.microsoft.amplifier.tool"


class Turn:
    def __init__(self):
        self.cancelled = False
        self.recovery = RecoveryState()
        self.effect_call_ids = set()
        self.tool_parts = {}
        self.events = []

    def emit(self, name, payload):
        self.events.append((name, payload))

    def stop(self, state, error):
        return PolicyStop(error)


async def forbidden(*args):
    raise AssertionError("A denied fixture must not execute a tool")


@pytest.mark.parametrize("source", ["built-in", "mcp", "caller", "unresolved-mcp"])
async def test_registry_identity_survives_binding_history_and_http_without_trusting_arguments(tmp_path, source):
    config = ResolvedConfig("fixture", "fixture", None, (), "deny", tmp_path, {}, None, None)
    expected = None
    if source == "mcp":
        connection = SimpleNamespace(
            server=McpServer("other_server", "stdio", command="unused"),
            client=None,
            shutdown=asyncio.Event(),
            call=forbidden,
        )
        definition = {"name": "work.report", "input_schema": {"type": "object"}}
        tool = registered_mcp_tool(SimpleNamespace(), cast(MCPConnection, connection), definition)
        expected = {"version": 1, "kind": "mcp", "server": "other_server", "name": "work.report"}
    else:
        tool = RegisteredTool(
            "write_file" if source == "built-in" else "mcp_minecraft_observe",
            "Fixture",
            {"$schema": SCHEMA, "type": "object"},
            forbidden,
            "mcp" if source == "unresolved-mcp" else source,
        )
        if source == "built-in":
            expected = {"version": 1, "kind": "builtin", "name": "write_file"}
    arguments = {
        "server": "minecraft",
        "tool": "observe",
        "_meta": {NAMESPACE: {"version": 1, "kind": "mcp", "server": "minecraft", "name": "observe"}},
    }
    turn = Turn()
    result = await execute_tool(turn, config, tool, "actual-call", arguments)
    assert result.outcome == "cancelled"
    call = next(payload.call for name, payload in turn.events if name == "tool_call")
    request = next(payload.request for name, payload in turn.events if name == "approval_request")
    assert call.arguments == request.arguments == arguments
    assert getattr(call, NAMESPACE, None) == getattr(request, NAMESPACE, None) == expected
    bridge = RecordBridge(_records)
    assert getattr(bridge.to_public(call), NAMESPACE, None) == expected
    assert getattr(bridge.to_public(request), NAMESPACE, None) == expected
    assert wire(bridge.to_public(call)).get(NAMESPACE) == expected
    saved = CommittedTurn(TurnRecord("turn", TurnInput([]), TurnResult("success"), [ToolActivity(call, result)]), 0)
    restored = CommittedTurn.loads(saved.dumps()).turn.activity
    assert restored is not None
    assert isinstance(restored[0], ToolActivity)
    assert getattr(restored[0].call, NAMESPACE, None) == expected
    if expected is not None:
        getattr(request, NAMESPACE)["name"] = "changed"
        assert getattr(call, NAMESPACE) == expected


async def test_invalid_schema_keeps_resolved_identity_but_never_reaches_approval(tmp_path):
    config = ResolvedConfig("fixture", "fixture", None, (), "deny", tmp_path, {}, None, None)
    tool = RegisteredTool("write_file", "Write", {"$schema": SCHEMA, "type": "object"}, forbidden, "built-in")
    tool = replace(tool, input_schema={"$schema": SCHEMA, "type": "object", "additionalProperties": False})
    turn = Turn()
    result = await execute_tool(turn, config, tool, "bad-schema", {"server": "minecraft"})
    assert result.error is not None
    assert result.error.code == "invalid_input"
    assert [name for name, payload in turn.events] == ["tool_call", "tool_result"]
    assert getattr(turn.events[0][1].call, NAMESPACE, None) == {"version": 1, "kind": "builtin", "name": "write_file"}
