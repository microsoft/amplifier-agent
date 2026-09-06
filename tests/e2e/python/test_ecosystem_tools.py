import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ApprovalResponse,
    McpServer,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.engine import provision, provision_many

SCHEMA = "https://json-schema.org/draft/2020-12/schema"
MCP_SERVICE = Path(__file__).parents[3] / "conformance/fixtures/mcp_service.py"


def options(**kwargs):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", **kwargs)


async def collect(agent):
    async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
        turn = await session.start_turn(TurnInput([TextPart("Perform the requested work.")]))
        return [event async for event in turn.events()]


@pytest.mark.parametrize("authority", ["allow", "deny"])
@pytest.mark.parametrize("name,arguments", [
    ("write_file", {"file_path": "effect.txt", "content": "written"}),
    ("bash", {"command": "printf written > effect.txt"}),
])
async def test_builtin_effects_follow_approval(monkeypatch, tmp_path, authority, name, arguments):
    monkeypatch.chdir(tmp_path)
    factory = provision(monkeypatch, [
        {"tool": {"name": name, "arguments": arguments}}, {"text": "Done"},
    ])
    requests = []

    async def approve(request):
        assert not (tmp_path / "effect.txt").exists()
        assert "written" in request.summary and "effect.txt" in request.summary
        assert str(tmp_path) in request.summary
        assert request.name == name and request.call_id
        requests.append(request)
        return ApprovalResponse(authority)

    async with await create_agent(options(approvals=approve)) as agent:
        monkeypatch.chdir(tmp_path.parent)
        events = await collect(agent)
    assert len(requests) == 1
    if authority == "allow":
        assert (tmp_path / "effect.txt").read_text() == "written"
    assert events[-1].payload.state == ("success" if authority == "allow" else "rejected")
    assert (tmp_path / "effect.txt").exists() == (authority == "allow")
    call = next(event.payload.call for event in events if event.type == "tool_call")
    assert call.source == "built-in"
    result = next(event.payload.resolution for event in events if event.type == "tool_result")
    assert result.call_id == call.call_id
    assert len(factory.requests) == (2 if authority == "allow" else 1)


async def test_bash_uses_captured_environment_and_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AA_CAPTURED", "original")
    provision(monkeypatch, [
        {"tool": {"name": "bash", "arguments": {"command": "printf %s \"$AA_CAPTURED\" > effect.txt"}}},
        {"text": "Done"},
    ])
    async with await create_agent(options(approvals="allow")) as agent:
        monkeypatch.setenv("AA_CAPTURED", "changed")
        monkeypatch.chdir(tmp_path.parent)
        events = await collect(agent)
    assert events[-1].payload.state == "success"
    assert (tmp_path / "effect.txt").read_text() == "original"


@pytest.mark.parametrize("authority", ["allow", "deny"])
async def test_web_fetch_approval_controls_actual_http_request(monkeypatch, authority):
    requests = []

    async def respond(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        body = b"<html><body>Fetched marker</body></html>"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provision(monkeypatch, [
            {"tool": {"name": "web_fetch", "arguments": {"url": f"http://127.0.0.1:{port}/"}}},
            {"text": "Done"},
        ])
        async with await create_agent(options(approvals=authority)) as agent:
            events = await collect(agent)
    assert len(requests) == (1 if authority == "allow" else 0)
    assert events[-1].payload.state == ("success" if authority == "allow" else "rejected")
    result = next(event.payload.resolution for event in events if event.type == "tool_result")
    if authority == "allow":
        assert "Fetched marker" in result.content


@pytest.mark.parametrize("tool,pattern,expected", [
    ("glob", "*.txt", "marker.txt"), ("grep", "Search marker", "Search marker"),
])
async def test_search_tools_read_captured_working_directory(monkeypatch, tmp_path, tool, pattern, expected):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marker.txt").write_text("Search marker\n")
    provision(monkeypatch, [
        {"tool": {"name": tool, "arguments": {"pattern": pattern}}}, {"text": "Done"},
    ])
    async with await create_agent(options(approvals="allow")) as agent:
        monkeypatch.chdir(tmp_path.parent)
        events = await collect(agent)
    assert events[-1].payload.state == "success"
    result = next(event.payload.resolution for event in events if event.type == "tool_result")
    assert expected in result.content


async def test_cancelled_shell_drains_process_and_reports_unknown(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    provision(monkeypatch, [{"tool": {"name": "bash", "arguments": {
        "command": "printf started > effect.txt; sleep 20; printf late > late.txt",
    }}}])
    async with await create_agent(options(approvals="allow")) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Run the command.")]))
            collecting = asyncio.create_task(_events(turn))
            async with asyncio.timeout(5):
                while not (tmp_path / "effect.txt").exists():
                    await asyncio.sleep(0.01)
            await turn.cancel()
            events = await collecting
    assert events[-1].payload.state == "cancelled"
    results = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert len(results) == 1 and results[0].outcome == "unknown"
    assert not (tmp_path / "late.txt").exists()


async def _events(turn):
    return [event async for event in turn.events()]


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("authority", ["allow", "deny"])
async def test_mcp_has_its_own_executor_and_approval(monkeypatch, tmp_path, transport, authority):
    ledger = tmp_path / "effects.jsonl"
    environment = {**os.environ, "MCP_LEDGER": str(ledger), "MCP_CAPTURED": "original"}
    process = None
    if transport == "stdio":
        server = McpServer("ledger", "stdio", command=sys.executable, args=[str(MCP_SERVICE)],
                           env={"MCP_LEDGER": str(ledger), "MCP_CAPTURED": "original"})
    else:
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(MCP_SERVICE), "--transport", "http", "--port", str(port),
            env=environment, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(5):
            while True:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.02)
        server = McpServer("ledger", "http", url=f"http://127.0.0.1:{port}/mcp")
    try:
        provision(monkeypatch, [
            {"tool": {"name": "mcp_ledger_record", "arguments": {"value": "once"}}},
            {"text": "Done"},
        ])
        requests = []

        async def approve(request):
            assert not ledger.exists()
            assert request.name == "mcp_ledger_record"
            assert '"value": "once"' in request.summary
            requests.append(request)
            return ApprovalResponse(authority)

        async with await create_agent(options(approvals=approve, mcp_servers=[server])) as agent:
            monkeypatch.setenv("MCP_CAPTURED", "changed")
            events = await collect(agent)
        assert events[-1].payload.state == ("success" if authority == "allow" else "rejected")
        call = next(event.payload.call for event in events if event.type == "tool_call")
        assert len(requests) == 1
        assert requests[0].call_id == call.call_id
        assert call.source == "mcp"
        assert ledger.exists() == (authority == "allow")
        if authority == "allow":
            records = [json.loads(line) for line in ledger.read_text().splitlines()]
            assert len(records) == 1
            assert records[0]["pid"] != os.getpid()
            assert records[0]["captured"] == "original"
            assert records[0]["value"] == "once"
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize("tool,code,effects", [
    ("fail", "tool_failed", 0), ("uncertain", "tool_completion_unknown", 1),
])
async def test_mcp_failed_and_lost_results_are_distinct(monkeypatch, tmp_path, tool, code, effects):
    ledger = tmp_path / "effects.jsonl"
    factory = provision(monkeypatch, [{"tool": {
        "name": f"mcp_ledger_{tool}", "arguments": {"value": "once"} if effects else {},
    }}])
    server = McpServer("ledger", "stdio", command=sys.executable, args=[str(MCP_SERVICE)],
                       env={"MCP_LEDGER": str(ledger)})
    async with await create_agent(options(approvals="allow", mcp_servers=[server])) as agent:
        events = await collect(agent)
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == code
    assert len(factory.requests) == 1
    assert (len(ledger.read_text().splitlines()) if ledger.exists() else 0) == effects


@pytest.mark.parametrize("deny_child", [False, True])
async def test_delegation_inherits_caller_authority_and_correlates_nested_calls(monkeypatch, deny_child):

    provision_many(monkeypatch,
        [{"tool": {"name": "delegate", "arguments": {"instruction": "Use counter.", "tools": ["counter"]}}},
         {"text": "Parent complete"}],
        [{"tool": {"name": "counter", "arguments": {}}}, {"text": "Child complete"}],
    )
    effects = []

    async def counter(arguments, context):
        effects.append((os.getpid(), context.call_id))
        return "counted"

    async def approval(request):
        return ApprovalResponse("deny" if deny_child and request.name == "counter" else "allow")

    async with await create_agent(options(approvals=approval, tools=[
        Tool("counter", "Record the child invocation.", {"$schema": SCHEMA, "type": "object"}, counter),
    ])) as agent:
        events = await collect(agent)
    assert events[-1].payload.state == ("rejected" if deny_child else "success")
    calls = [event.payload.call for event in events if event.type == "tool_call"]
    results = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert len(calls) == len(results) == 2
    assert len({call.call_id for call in calls}) == 2
    assert {call.call_id for call in calls} == {result.call_id for result in results}
    assert len(effects) == (0 if deny_child else 1)
    if effects:
        assert effects[0][0] == os.getpid()
        assert events[-1].payload.usage.entries[0].tokens_in == 28


async def test_skill_shell_preprocessing_is_a_separate_approved_effect(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    skill = tmp_path / "skills" / "record"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: record\ndescription: Record a marker.\n---\n"
        "!`printf recorded > effect.txt; printf marker`\nArguments: $ARGUMENTS\n"
    )
    factory = provision(monkeypatch, [
        {"tool": {"name": "load_skill", "arguments": {"name": "record", "arguments": "$(touch injected.txt)"}}},
        {"text": "Done"},
    ])
    async with await create_agent(options(approvals="allow", skills=[str(skill.parent)])) as agent:
        events = await collect(agent)
    assert events[-1].payload.state == "success"
    assert (skill / "effect.txt").read_text() == "recorded"
    assert not (skill / "injected.txt").exists()
    calls = [event.payload.call.name for event in events if event.type == "tool_call"]
    assert calls == ["load_skill", "bash"]
    assert len([event for event in events if event.type == "tool_result"]) == 2
    assert "$(touch injected.txt)" in str(factory.requests[-1]["messages"])


@pytest.mark.parametrize("executor", ["bash", "mcp_ledger_record"])
@pytest.mark.parametrize("decision,state,code", [
    ("cancel", "cancelled", "approval_cancelled"),
    ("invalid", "failure", "approval_invalid"),
    ("timeout", "failure", "approval_timeout"),
    ("unavailable", "failure", "approval_unavailable"),
])
async def test_approval_failures_never_execute_builtin_or_mcp(
    monkeypatch, tmp_path, executor, decision, state, code,
):
    from amplifier_agent_engine._engine import effects

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(effects, "APPROVAL_TIMEOUT_SECONDS", 0.02)
    ledger = tmp_path / "effects.jsonl"
    server = McpServer("ledger", "stdio", command=sys.executable, args=[str(MCP_SERVICE)],
                       env={"MCP_LEDGER": str(ledger)})

    async def approval(request):
        if decision == "timeout":
            await asyncio.Event().wait()
        if decision == "invalid":
            return {"decision": "allow"}
        return ApprovalResponse("cancel")

    provision(monkeypatch, [{"tool": {
        "name": executor,
        "arguments": {"command": "printf effect > effects.jsonl"} if executor == "bash"
                     else {"value": "effect"},
    }}])
    async with await create_agent(options(
        approvals=None if decision == "unavailable" else approval,
        mcp_servers=[server] if executor.startswith("mcp_") else [],
    )) as agent:
        events = await collect(agent)
    assert events[-1].payload.state == state
    assert events[-1].payload.error.code == code
    assert not ledger.exists()
    types = [event.type for event in events]
    assert types.count("tool_call") == types.count("tool_result") == 1
    assert types.count("approval_request") == types.count("approval_decision") == 1


async def test_fork_skill_runs_a_child_model_and_accounts_for_it(monkeypatch, tmp_path):

    skill = tmp_path / "child"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: child\ndescription: Answer in a child.\ncontext: fork\n"
        "model: claude-sonnet-5\n---\nReturn the given argument: $ARGUMENTS\n"
    )
    factory = provision_many(monkeypatch,
        [{"tool": {"name": "load_skill", "arguments": {"name": "child", "arguments": "marker"}}},
         {"text": "Parent complete"}],
        [{"text": "Child marker"}],
    )
    async with await create_agent(options(approvals="allow", skills=[str(skill)])) as agent:
        events = await collect(agent)
    assert events[-1].payload.state == "success"
    assert len(factory) == 2
    assert "marker" in str(factory[1].requests[0]["messages"])
    assert events[-1].payload.usage.entries[0].tokens_in == 21
    assert [event.payload.call.name for event in events if event.type == "tool_call"] == ["load_skill"]


async def test_caller_name_collision_with_builtin_is_refused(monkeypatch):
    factory = provision(monkeypatch, [{"text": "Unreachable"}])

    async def handler(arguments, context):
        raise AssertionError("A refused declaration must never execute.")

    with pytest.raises(AgentError) as caught:
        await create_agent(options(tools=[Tool(
            "bash", "Collides with a built-in.", {"$schema": SCHEMA, "type": "object"}, handler,
        )]))
    assert caught.value.code == "invalid_input"
    assert factory.requests == []


@pytest.mark.production_only
async def test_malformed_builtin_output_is_a_named_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from amplifier_module_tool_filesystem import WriteTool

    async def broken(self, arguments):
        return SimpleNamespace(success=True, output={"bad": float("nan")})

    monkeypatch.setattr(WriteTool, "execute", broken)
    monkeypatch.chdir(tmp_path)
    factory = provision(monkeypatch, [{"tool": {
        "name": "write_file", "arguments": {"file_path": "effect.txt", "content": "value"},
    }}])
    async with await create_agent(options(approvals="allow")) as agent:
        events = await collect(agent)
    assert events[-1].payload.error.code == "tool_result_invalid"
    result = next(event.payload.resolution for event in events if event.type == "tool_result")
    assert result.outcome == "unknown"
    assert len(factory.requests) == 1


@pytest.mark.production_only
async def test_economy_delegation_accounts_for_actual_child_model(monkeypatch):

    provision_many(monkeypatch,
        [{"tool": {"name": "delegate", "arguments": {"instruction": "Answer briefly.", "model_role": "economy"}}},
         {"text": "Parent complete"}],
        [{"text": "Child complete"}],
    )
    async with await create_agent(AgentOptions(
        provider="anthropic", model="claude-opus-5", approvals="allow",
    )) as agent:
        events = await collect(agent)
    result = events[-1].payload
    assert result.state == "success"
    assert {entry.model: entry.tokens_in for entry in result.usage.entries} == {
        "claude-opus-5": 14, "claude-sonnet-5": 7,
    }


async def test_expensive_delegation_is_rejected_before_child_work(monkeypatch):

    factory = provision_many(monkeypatch,
        [{"tool": {"name": "delegate", "arguments": {"instruction": "Answer briefly.", "model": "claude-opus-5"}}}],
        [{"text": "Unreachable"}],
    )
    async with await create_agent(options(approvals="allow")) as agent:
        events = await collect(agent)
    assert events[-1].payload.error.code == "selector_rejected"
    assert len(factory) == 1
