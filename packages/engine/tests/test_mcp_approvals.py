"""Prepared MCP calls use the owned public client and the existing effect policy."""

import asyncio
from contextlib import contextmanager
import copy
from dataclasses import replace
from types import SimpleNamespace

from amplifier_agent_engine._engine.configuration import ResolvedConfig
from amplifier_agent_engine._engine.mcp_tools import MCPConnection, prepare_mcp
from amplifier_agent_engine._engine.tools import ToolRegistry
from amplifier_agent_engine._records import (
    AgentError,
    McpServer,
    ToolContext,
    ToolFailed,
    ToolOutcomeUnknown,
    _ToolNotExecutedError,
)
from amplifier_module_tool_mcp.sdk_compat import MCPProtocolError
from mcp.types import CallToolResult, TextContent
import pytest


class Client:
    def __init__(self, *arguments, **options):
        self.options = options
        self.arguments = arguments
        self.is_connected = False
        self.connections = 0
        self.disconnections = 0
        self.calls = []
        self.binding = {"version": 1, "key": "a" * 64, "httpSendAuthorization": 1}
        self.tools = [{"name": "record", "description": "Record", "input_schema": {"type": "object"}}]
        self.result = CallToolResult(content=[TextContent(type="text", text="complete")], is_error=False)
        self.error = None
        self.predicate = None
        self.before_call = lambda: None
        self.retired = False

    def retire(self):
        self.retired = True

    async def connect(self):
        self.connections += 1
        self.is_connected = True

    async def disconnect(self):
        self.disconnections += 1
        self.is_connected = False

    def get_tools(self):
        return self.tools

    def approval_binding(self):
        if not self.is_connected:
            raise ValueError("Unavailable")
        return copy.deepcopy(self.binding)

    @contextmanager
    def approval_scope(self, expected, name, arguments, is_current=None):
        assert expected == self.approval_binding()
        if is_current is not None and is_current() is not True:
            raise ValueError("Owner changed")
        self.predicate = is_current
        try:
            yield
        finally:
            self.predicate = None

    async def call_tool(self, name, arguments, reconnect=True):
        assert reconnect is False
        self.before_call()
        if not self.is_connected or (self.predicate is not None and self.predicate() is not True):
            raise ValueError("No current invocation")
        self.calls.append((name, copy.deepcopy(arguments)))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def clients(monkeypatch):
    from amplifier_module_tool_mcp import client, streamable_http_client

    created = []

    def create(*args, **kwargs):
        instance = Client(*args, **kwargs)
        created.append(instance)
        return instance

    monkeypatch.setattr(client, "MCPClient", create)
    monkeypatch.setattr(streamable_http_client, "MCPStreamableHTTPClient", create)
    return created


async def runtime_at(path, transport="stdio", environment=None, headers=None):
    server = McpServer("fixture", "stdio", command="/nonexistent-fixture-command", env={"OVERRIDE": "server"})
    if transport == "http":
        server = McpServer(
            "fixture", "http", url="http://127.0.0.1:1/mcp", headers=headers or {"Authorization": "fixture"}
        )
    config = ResolvedConfig(
        "fixture",
        "fixture",
        None,
        (),
        None,
        path / "sessions",
        {},
        None,
        None,
        working_directory=path,
        environment=environment or {"CAPTURED": "original", "OVERRIDE": "parent"},
        mcp_servers=(server,),
    )
    runtime = SimpleNamespace(config=config, registry=ToolRegistry())
    await prepare_mcp(runtime, runtime.registry)
    return runtime


async def test_owned_mcp_tool_exposes_engine_authored_scope_without_network(monkeypatch, tmp_path):
    from amplifier_agent_engine._engine import mcp_tools

    class Facade:
        def __init__(self, server, environment, working_directory):
            self.server = server
            self.environment = environment
            self.working_directory = working_directory
            self.client = Client()
            self.tools = self.client.get_tools()
            self.shutdown = asyncio.Event()

        async def connect(self):
            await self.client.connect()

        async def close(self):
            await self.client.disconnect()

        async def call(self, name, arguments):
            return "fixture"

    monkeypatch.setattr(mcp_tools, "MCPConnection", Facade)
    runtime = await runtime_at(tmp_path)
    try:
        tool = runtime.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        prepared = tool.prepare({"value": "fixture"}, lambda: True)
        assert prepared is not None
        assert prepared.approval.producer == "com.microsoft.amplifier-agent.approvals/1"
        assert len(prepared.approval.key) == 64
    finally:
        await runtime.registry.close()


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_public_client_preserves_discovery_timeout_and_captured_spawn(clients, tmp_path, transport):
    runtime = await runtime_at(tmp_path, transport)
    try:
        client = clients[0]
        assert client.options["discovery"] == "tools"
        assert client.options["read_timeout_seconds"] == 120.0
        assert client.options["redact_connection_details"] is True
        if transport == "stdio":
            assert client.options["cwd"] == tmp_path
            assert client.options["inherit_environment"] is False
            assert client.options["env"] == {"CAPTURED": "original", "OVERRIDE": "server"}
            assert client.options["verbose_servers"] is True
        assert runtime.registry.tools["mcp_fixture_record"].prepare is not None
    finally:
        await runtime.registry.close()
    assert clients[0].disconnections == 1
    assert clients[0].retired is True


async def test_scope_matches_across_requests_and_keeps_secrets_private(clients, tmp_path):
    first, second = await runtime_at(tmp_path), await runtime_at(tmp_path)
    try:

        def scope(runtime):
            tool = runtime.registry.tools["mcp_fixture_record"]
            assert tool.prepare is not None
            return tool.prepare({"value": "secret"}, lambda: True)

        one, two = scope(first), scope(second)
        assert one is not None
        assert two is not None
        assert one.approval == two.approval
        assert "secret" not in one.approval.description
        assert one.approval.authority_lifetime == "request"
    finally:
        await first.registry.close()
        await second.registry.close()


@pytest.mark.parametrize("authority", ["environment", "authorization"])
async def test_new_mcp_authority_requires_a_distinct_remembered_key(clients, tmp_path, authority):
    first = await runtime_at(tmp_path, "http")
    second = await runtime_at(
        tmp_path,
        "http",
        environment={"CAPTURED": "different", "OVERRIDE": "parent"} if authority == "environment" else None,
        headers={"Authorization": "different"} if authority == "authorization" else None,
    )
    try:
        one = first.registry.tools["mcp_fixture_record"].prepare
        two = second.registry.tools["mcp_fixture_record"].prepare
        assert one is not None
        assert two is not None
        original, changed = one({}, lambda: True), two({}, lambda: True)
        assert original is not None
        assert changed is not None
        assert original.approval.key != changed.approval.key
        assert "different" not in changed.approval.description
    finally:
        await first.registry.close()
        await second.registry.close()


@pytest.mark.parametrize("change", ["connection", "schema", "registry", "environment", "configuration", "owner"])
async def test_prepared_scope_refuses_changed_authority_before_call(clients, tmp_path, change):
    runtime = await runtime_at(tmp_path)
    allowed = True
    try:
        tool = runtime.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        prepared = tool.prepare({"value": "original"}, lambda: allowed)
        assert prepared is not None
        if change == "connection":
            clients[0].binding["key"] = "b" * 64
        elif change == "schema":
            clients[0].tools[0]["input_schema"]["description"] = "changed"
        elif change == "registry":
            runtime.registry.tools.pop(tool.name)
        elif change == "environment":
            runtime.config.environment["CAPTURED"] = "changed"
        elif change == "configuration":
            runtime.config = replace(runtime.config, tool_result_max_bytes=10)
        else:
            allowed = False
        with pytest.raises(_ToolNotExecutedError):
            await prepared.handler({}, ToolContext("new"))
        assert clients[0].calls == []
    finally:
        await runtime.registry.close()


async def test_prepared_invocation_freezes_arguments_and_refuses_reuse(clients, tmp_path):
    runtime = await runtime_at(tmp_path)
    try:
        tool = runtime.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        arguments = {"value": "original"}
        prepared = tool.prepare(arguments, lambda: True)
        assert prepared is not None
        arguments["value"] = "changed"
        await prepared.handler(arguments, ToolContext("one"))
        with pytest.raises(_ToolNotExecutedError):
            await prepared.handler({}, ToolContext("two"))
        assert clients[0].calls == [("record", {"value": "original"})]
    finally:
        await runtime.registry.close()


async def test_refused_scope_cannot_be_revived_or_rebound_to_a_replacement_tool(clients, tmp_path):
    runtime = await runtime_at(tmp_path)
    try:
        tool = runtime.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        prepared = tool.prepare({}, lambda: True)
        assert prepared is not None
        runtime.registry.tools[tool.name] = replace(tool, description="replacement")
        assert tool.prepare({}, lambda: True) is None
        with pytest.raises(_ToolNotExecutedError):
            await prepared.handler({}, ToolContext("one"))
        runtime.registry.tools[tool.name] = tool
        with pytest.raises(_ToolNotExecutedError):
            await prepared.handler({}, ToolContext("two"))
        assert clients[0].calls == []
    finally:
        await runtime.registry.close()


async def test_child_uses_its_supplied_owner_gate_with_the_owned_parent_connection(clients, tmp_path):
    parent = await runtime_at(tmp_path)
    child = SimpleNamespace(config=parent.config, registry=ToolRegistry(), parent_registry=parent.registry)
    try:
        await prepare_mcp(child, child.registry)
        tool = child.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        allowed = True
        prepared = tool.prepare({}, lambda: allowed)
        assert prepared is not None
        allowed = False
        with pytest.raises(_ToolNotExecutedError):
            await prepared.handler({}, ToolContext("child"))
        assert clients[0].calls == []
        assert child.registry.cleanups == []
    finally:
        await parent.registry.close()


async def test_public_scope_keeps_owner_veto_until_client_admission(clients, tmp_path):
    runtime = await runtime_at(tmp_path)
    allowed = True

    def withdraw():
        nonlocal allowed
        allowed = False

    try:
        tool = runtime.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        prepared = tool.prepare({}, lambda: allowed)
        assert prepared is not None
        clients[0].before_call = withdraw
        # A generic public-client error is conservatively unknown. Only the
        # client's own fixture ledger establishes no admission in this case.
        with pytest.raises(ToolOutcomeUnknown):
            await prepared.handler({}, ToolContext("one"))
        assert clients[0].calls == []
        with pytest.raises(_ToolNotExecutedError):
            await prepared.handler({}, ToolContext("two"))
    finally:
        await runtime.registry.close()


@pytest.mark.parametrize("unsupported", ["missing", "malformed", "http_without_send_witness"])
async def test_unsupported_binding_keeps_once_call_without_reconnection(clients, tmp_path, unsupported):
    runtime = await runtime_at(tmp_path, "http")
    try:
        client = clients[0]
        if unsupported == "missing":
            client.approval_binding = None
        elif unsupported == "malformed":
            client.binding = {"version": 9, "key": "a" * 64}
        else:
            client.binding.pop("httpSendAuthorization")
        tool = runtime.registry.tools["mcp_fixture_record"]
        assert tool.prepare is not None
        assert tool.prepare({}, lambda: True) is None
        await tool.handler({}, ToolContext("once"))
        assert client.calls == [("record", {})]
        assert client.connections == 1
    finally:
        await runtime.registry.close()


@pytest.mark.parametrize("when", ["before", "at_admission"])
async def test_unscoped_unavailable_connection_never_reconnects(clients, tmp_path, when):
    runtime = await runtime_at(tmp_path)
    try:
        client = clients[0]
        if when == "before":
            client.is_connected = False
        else:
            client.before_call = lambda: setattr(client, "is_connected", False)
        with pytest.raises((AgentError, ToolOutcomeUnknown)):
            await runtime.registry.tools["mcp_fixture_record"].handler({}, ToolContext("once"))
        assert client.connections == 1
        assert client.calls == []
    finally:
        await runtime.registry.close()


@pytest.mark.parametrize("code", [-32700, -32600, -32601, -32602, -32603, -32000])
async def test_protocol_rejection_and_unknown_are_not_conflated(clients, tmp_path, code):
    runtime = await runtime_at(tmp_path)
    try:
        clients[0].error = MCPProtocolError(
            "fixture rejection", code=code, message="fixture", data=None, explanation="fixture"
        )
        expected = ToolOutcomeUnknown if code == -32000 else ToolFailed
        with pytest.raises(expected) as raised:
            await runtime.registry.tools["mcp_fixture_record"].handler({}, ToolContext("one"))
        if code != -32000:
            assert str(raised.value) == "fixture"
        assert len(clients[0].calls) == 1
    finally:
        await runtime.registry.close()


async def test_cancellation_propagates_and_connection_cleanup_is_joined(clients, tmp_path):
    connection = MCPConnection(McpServer("fixture", "stdio", command="/nonexistent-fixture-command"), {}, tmp_path)
    await connection.connect()
    clients[0].error = asyncio.CancelledError()
    try:
        with pytest.raises(asyncio.CancelledError):
            await connection.call("record", {})
    finally:
        await connection.close()
    assert clients[0].disconnections == 1
