import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ApprovalResponse,
    McpServer,
    SessionOptions,
    TextPart,
    Tool,
    ToolFailed,
    ToolOutcomeUnknown,
    TurnInput,
    create_agent,
)
from amplifier_core.message_models import ToolCall

from conformance.fixtures.scripted_provider import ScriptedFactory

SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}


def call(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


def provision(monkeypatch, *scripts):
    from amplifier_agent_engine._engine import assembly

    factories = []

    async def factory(config, coordinator):
        script = scripts[len(factories)]
        probe = ScriptedFactory(script)
        factories.append(probe)
        provider = await probe(config, coordinator)
        complete = provider.complete

        async def observed(request, **kwargs):
            step = script[provider.index]
            response = await complete(request, **kwargs)
            if "tools" in step:
                response.tool_calls = [
                    ToolCall(id=f"call-{provider.index}-{index}", name=value["name"],
                             arguments=value.get("arguments", {}))
                    for index, value in enumerate(step["tools"])
                ]
            return response

        provider.complete = observed
        return provider

    monkeypatch.setattr(assembly, "_provider_factory", factory)
    return factories


def options(**kwargs):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow",
                        tool_error_policy="continue", **kwargs)


async def collect(session):
    turn = await session.start_turn(TurnInput([TextPart("Perform the requested work.")]))
    return [event async for event in turn.events()]


def resolutions(events):
    calls = [event.payload.call.call_id for event in events if event.type == "tool_call"]
    results = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert len(calls) == len(results) == len(set(calls))
    assert set(calls) == {result.call_id for result in results}
    requests = [event.payload.request.request_id for event in events if event.type == "approval_request"]
    decisions = [event.payload.resolution.request_id for event in events if event.type == "approval_decision"]
    assert len(requests) == len(decisions) == len(set(requests))
    assert set(requests) == set(decisions)
    assert [event.type for event in events].count("terminal") == 1
    return results


@pytest.mark.parametrize("failure,outcome,code", [
    (ToolFailed("Execution failed"), "failed", "tool_failed"),
    (ToolOutcomeUnknown("Execution uncertain"), "unknown", "tool_completion_unknown"),
])
async def test_recoverable_error_reaches_model_and_preserves_history(monkeypatch, failure, outcome, code):
    factories = provision(monkeypatch, [call("effect"), {"text": "Explained failure"}, {"text": "Next turn"}])
    effects = []

    async def effect(arguments, context):
        effects.append(context.call_id)
        raise failure

    async with await create_agent(options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
            assert events[-1].payload.state == "success"
            await collect(session)
    result, = resolutions(events)
    assert result.outcome == outcome
    assert result.error.code == code
    assert effects == [result.call_id]
    assert len(factories[0].requests) == 3
    first_offered = {tool["name"] for tool in factories[0].requests[0]["tools"]}
    next_offered = {tool["name"] for tool in factories[0].requests[1]["tools"]}
    assert "effect" in first_offered
    assert next_offered == ({"read_file", "glob", "grep"} if outcome == "unknown" else first_offered)
    assert {tool["name"] for tool in factories[0].requests[2]["tools"]} == first_offered
    for request in factories[0].requests[1:]:
        message, = [message for message in request["messages"] if message["role"] == "tool"]
        content = json.loads(message["content"])
        if "success" in content:
            assert content["success"] is False
            assert content["error"] == vars(result.error)
            content = content["output"]
        assert content == {"call_id": result.call_id, "outcome": outcome,
                           "content": None, "error": vars(result.error)}
        assert message["tool_call_id"] == result.call_id


@pytest.mark.parametrize("failure,code", [
    (RuntimeError("Callback failed"), "tool_callback_failed"),
    (AgentError("tool_failed", "executor", "Guard failed", "Repair the guard."), "tool_callback_failed"),
    (None, "tool_result_invalid"),
])
async def test_protocol_errors_remain_fatal_under_continue(monkeypatch, failure, code):
    factories = provision(monkeypatch, [call("effect"), {"text": "Must not continue"}])

    async def effect(arguments, context):
        if failure:
            raise failure
        return {"invalid": "result"}

    async with await create_agent(options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == code
    assert len(factories[0].requests) == 1
    resolutions(events)


@pytest.mark.parametrize("policy", ["stop", "continue"])
async def test_bash_timeout_retains_partial_streams_and_drains_process(monkeypatch, tmp_path, policy):
    monkeypatch.chdir(tmp_path)
    factories = provision(monkeypatch, [
        call("bash", command="printf partial-out; printf partial-err >&2; printf begun > begun; sleep 5; printf late > late", timeout=1),
        {"text": "The command timed out; its earlier effects remain."},
    ])
    settings = options()
    settings.tool_error_policy = policy
    async with await create_agent(settings) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    result, = resolutions(events)
    assert result.outcome == "unknown"
    assert result.error.code == "tool_completion_unknown"
    output = json.loads(result.content)
    assert output["stdout"] == "partial-out"
    assert output["stderr"] == "partial-err"
    assert output["returncode"] != 0
    assert (tmp_path / "begun").read_text() == "begun"
    assert not (tmp_path / "late").exists()
    assert events[-1].payload.state == ("success" if policy == "continue" else "failure")
    assert len(factories[0].requests) == (2 if policy == "continue" else 1)


@pytest.mark.parametrize("attempt", [
    call("effect"),
    call("effect", spelling="different arguments"),
    call("bash", command="printf repeated > repeated"),
    call("bash", command="  printf '%s' repeated > ./repeated "),
    call("write_file", file_path="repeated", content="repeated"),
    call("edit_file", file_path="existing", old_string="before", new_string="after"),
    call("delegate", instruction="Repeat the effect with another tool"),
    call("web_fetch", url="https://example.invalid/"),
])
async def test_unknown_blocks_new_effects_and_new_turn_restores_authority(monkeypatch, tmp_path, attempt):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing").write_text("before")
    factories = provision(monkeypatch, [call("effect"), attempt, call("effect"), {"text": "New work completed"}])
    effects = []

    async def effect(arguments, context):
        effects.append(context.call_id)
        if len(effects) == 1:
            raise ToolOutcomeUnknown("The effect may have landed")
        return "Explicit new turn completed"

    async with await create_agent(options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
            assert len(effects) == 1
            followup = await collect(session)
    first, blocked = resolutions(events)
    assert first.outcome == "unknown"
    assert blocked.outcome == "cancelled"
    assert blocked.error.code == "tool_recovery_blocked"
    assert blocked.error.details == {"uncertain_call_id": first.call_id}
    assert events[-1].payload.error == blocked.error
    assert events[-1].payload.state == "failure"
    assert followup[-1].payload.state == "success"
    assert len(effects) == 2
    assert len(factories[0].requests) == 4
    assert not (tmp_path / "repeated").exists()
    assert (tmp_path / "existing").read_text() == "before"


@pytest.mark.parametrize("inspection", [
    call("read_file", file_path="receipt.txt"),
    call("glob", pattern="*.txt"),
    call("grep", pattern="receipt", path="."),
])
async def test_unknown_allows_approved_local_inspection(monkeypatch, tmp_path, inspection):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "receipt.txt").write_text("receipt exists")
    provision(monkeypatch, [call("effect"), inspection, {"text": "Inspected the local receipt"}])

    async def effect(arguments, context):
        raise ToolOutcomeUnknown("Receipt creation uncertain")

    async with await create_agent(options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    first, inspection_result = resolutions(events)
    assert first.outcome == "unknown"
    assert inspection_result.outcome == "completed"
    assert "receipt" in inspection_result.content
    assert events[-1].payload.state == "success"


async def test_unknown_blocks_a_sibling_waiting_for_approval(monkeypatch):
    provision(monkeypatch, [{"tools": [{"name": "uncertain"}, {"name": "waiting"}]}])
    waiting = asyncio.Event()
    callback_drained = asyncio.Event()
    effects = []

    async def approve(request):
        if request.name == "waiting":
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                callback_drained.set()
        return ApprovalResponse("allow")

    async def uncertain(arguments, context):
        await waiting.wait()
        raise ToolOutcomeUnknown("Unknown sibling effect")

    async def effect(arguments, context):
        effects.append(context.call_id)
        return "Must not execute"

    settings = options(tools=[Tool("uncertain", "Uncertain work.", SCHEMA, uncertain),
                              Tool("waiting", "Waiting work.", SCHEMA, effect)])
    settings.approvals = approve
    async with await create_agent(settings) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            async with asyncio.timeout(5):
                events = await collect(session)
    assert callback_drained.is_set()
    assert not effects
    results = resolutions(events)
    assert {result.outcome for result in results} == {"unknown", "cancelled"}
    assert events[-1].payload.error.code == "tool_recovery_blocked"


@pytest.mark.parametrize("blocked_sibling", [False, True])
async def test_unknown_does_not_cancel_already_executing_sibling(monkeypatch, blocked_sibling):
    calls = [{"name": "uncertain"}, {"name": "running"}]
    if blocked_sibling:
        calls.append({"name": "waiting"})
    provision(monkeypatch, [{"tools": calls}, {"text": "Both results recorded"}])
    started = asyncio.Event()
    settled = asyncio.Event()
    waiting = asyncio.Event()

    async def approve(request):
        if request.name == "waiting":
            waiting.set()
            await asyncio.Event().wait()
        return ApprovalResponse("allow")

    async def uncertain(arguments, context):
        await started.wait()
        if blocked_sibling:
            await waiting.wait()
        raise ToolOutcomeUnknown("Uncertain result")

    async def running(arguments, context):
        started.set()
        await asyncio.sleep(0.05)
        settled.set()
        return "Authoritative completion"

    settings = options(tools=[Tool("uncertain", "Uncertain work.", SCHEMA, uncertain),
                              Tool("running", "Started work.", SCHEMA, running),
                              Tool("waiting", "Waiting work.", SCHEMA, running)])
    settings.approvals = approve
    async with await create_agent(settings) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    assert settled.is_set()
    assert {result.outcome for result in resolutions(events)} == (
        {"unknown", "completed", "cancelled"} if blocked_sibling else {"unknown", "completed"}
    )
    assert events[-1].payload.state == ("failure" if blocked_sibling else "success")


@pytest.mark.parametrize("command", ["exit 7", "printf '%s' '{\"decision\":\"block\"}'", "printf '{invalid'", "sleep 5"])
async def test_skill_guard_errors_never_recover(monkeypatch, tmp_path, command):
    skill = tmp_path / "guard"
    skill.mkdir()
    header = {"name": "guard", "description": "Guard tool execution.", "hooks": {
        "PreToolUse": [{"matcher": "effect", "hooks": [{"type": "command", "command": command, "timeout": 1}]}],
    }}
    (skill / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\nGuard the work.\n")
    factories = provision(monkeypatch, [call("load_skill", name="guard"), call("effect"), {"text": "Must not continue"}])
    effects = []

    async def effect(arguments, context):
        effects.append(context.call_id)
        return "Must not execute"

    async with await create_agent(options(skills=[str(skill)], tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    assert not effects
    assert len(factories[0].requests) == 2
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == ("tool_completion_unknown" if command == "sleep 5" else "tool_failed")
    resolutions(events)


async def test_delegated_unknown_restricts_the_root_turn(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    factories = provision(monkeypatch,
              [call("delegate", instruction="Perform delegated work"), call("bash", command="printf forbidden > forbidden")],
              [call("effect"), {"text": "Delegated effect uncertain"}])

    async def effect(arguments, context):
        raise ToolOutcomeUnknown("Delegated effect uncertain")

    async with await create_agent(options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    results = resolutions(events)
    uncertain, = [result for result in results if result.outcome == "unknown"]
    blocked, = [result for result in results if result.error and result.error.code == "tool_recovery_blocked"]
    assert blocked.error.details["uncertain_call_id"] == uncertain.call_id
    assert not (tmp_path / "forbidden").exists()
    assert events[-1].payload.state == "failure"
    for factory in factories:
        assert {tool["name"] for tool in factory.requests[1]["tools"]} == {"read_file", "glob", "grep"}


async def test_recovery_drains_nested_effect_and_its_started_delegate(monkeypatch, tmp_path):
    skill = tmp_path / "nested"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: nested\ndescription: Perform nested work.\ncontext: fork\n---\n"
        "Prepared context: !`printf prepared`\nPerform delegated work.\n"
    )
    provision(monkeypatch, [{"tools": [
        {"name": "load_skill", "arguments": {"name": "nested"}},
        {"name": "uncertain"}, {"name": "waiting"},
    ]}], [call("running"), {"text": "Delegated work completed"}])
    started = asyncio.Event()
    waiting = asyncio.Event()
    completed = asyncio.Event()

    async def approve(request):
        if request.name == "waiting":
            waiting.set()
            await asyncio.Event().wait()
        return ApprovalResponse("allow")

    async def uncertain(arguments, context):
        await started.wait()
        await waiting.wait()
        raise ToolOutcomeUnknown("Root effect uncertain")

    async def running(arguments, context):
        started.set()
        await asyncio.sleep(0.05)
        completed.set()
        return "Nested effect completed"

    async def forbidden(arguments, context):
        raise AssertionError("Unapproved sibling executed")

    settings = options(skills=[str(skill)], tools=[Tool("uncertain", "Uncertain work.", SCHEMA, uncertain),
                              Tool("running", "Nested work.", SCHEMA, running),
                              Tool("waiting", "Waiting work.", SCHEMA, forbidden)])
    settings.approvals = approve
    async with await create_agent(settings) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            async with asyncio.timeout(5):
                events = await collect(session)
    assert completed.is_set()
    calls = {event.payload.call.name: event.payload.call.call_id
             for event in events if event.type == "tool_call"}
    results = {result.call_id: result for result in resolutions(events)}
    assert results[calls["running"]].outcome == "completed"
    assert results[calls["bash"]].outcome == "completed"
    assert results[calls["load_skill"]].outcome == "completed"
    assert results[calls["uncertain"]].outcome == "unknown"
    assert results[calls["waiting"]].outcome == "cancelled"
    assert events[-1].payload.error.code == "tool_recovery_blocked"


async def test_accepted_cancellation_stops_recovery_before_more_model_work(monkeypatch):
    factories = provision(monkeypatch, [call("effect"), {"text": "Must not continue"}])
    entered = asyncio.Event()

    async def effect(arguments, context):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ToolOutcomeUnknown("Cancellation left the effect uncertain") from None

    async with await create_agent(options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Perform work")]))
            await entered.wait()
            await turn.cancel()
            events = [event async for event in turn.events()]
    result, = resolutions(events)
    assert result.outcome == "unknown"
    assert events[-1].payload.state == "cancelled"
    assert len(factories[0].requests) == 1


async def test_local_inspection_retains_approval_veto(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "receipt").write_text("receipt")
    provision(monkeypatch, [call("effect"), call("read_file", file_path="receipt"), {"text": "Must not continue"}])

    async def effect(arguments, context):
        raise ToolOutcomeUnknown("Receipt creation uncertain")

    def approve(request):
        reply = asyncio.get_running_loop().create_future()
        reply.set_result(ApprovalResponse("deny" if request.name == "read_file" else "allow"))
        return reply

    settings = options(tools=[Tool("effect", "Perform work.", SCHEMA, effect)])
    settings.approvals = approve
    async with await create_agent(settings) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    assert events[-1].payload.state == "rejected"
    assert events[-1].payload.error.code == "approval_denied"
    assert [result.outcome for result in resolutions(events)] == ["unknown", "cancelled"]


@pytest.mark.parametrize("attempt", ["load", "inspect"])
async def test_unknown_blocks_skill_execution_without_bypassing_inspection_guard(monkeypatch, tmp_path, attempt):
    skill = tmp_path / "guard"
    skill.mkdir()
    (tmp_path / "receipt").write_text("receipt")
    header = {"name": "guard", "description": "Guard file reads.", "hooks": {
        "PreToolUse": [{"matcher": "read_file", "hooks": [{"type": "command", "command": "printf forbidden > forbidden"}]}],
    }}
    (skill / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\nGuard file inspection.\n")
    script = [call("effect"), call("load_skill", name="guard")]
    if attempt == "inspect":
        script = [call("load_skill", name="guard"), call("effect"), call("read_file", file_path=str(tmp_path / "receipt"))]
    provision(monkeypatch, script)

    async def effect(arguments, context):
        raise ToolOutcomeUnknown("Earlier effect uncertain")

    async with await create_agent(options(skills=[str(skill)], tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == "tool_recovery_blocked"
    assert not (skill / "forbidden").exists()
    assert resolutions(events)[-1].outcome == "cancelled"


@pytest.mark.parametrize("first", ["fail", "uncertain", "caller"])
async def test_mcp_recovery_preserves_executor_results_and_blocks_new_mcp_work(monkeypatch, tmp_path, first):
    ledger = tmp_path / "ledger.jsonl"
    service = Path(__file__).parents[2] / "conformance" / "fixtures" / "mcp_service.py"
    server = McpServer("ledger", "stdio", command=sys.executable, args=[str(service)], env={"MCP_LEDGER": str(ledger)})
    script = [call(f"mcp_ledger_{first}", **({"value": "once"} if first == "uncertain" else {})), {"text": "Recorded executor result"}]
    if first == "caller":
        script = [call("effect"), call("mcp_ledger_record", value="forbidden")]
    factories = provision(monkeypatch, script)

    async def effect(arguments, context):
        raise ToolOutcomeUnknown("Earlier effect uncertain")

    async with await create_agent(options(mcp_servers=[server], tools=[Tool("effect", "Perform work.", SCHEMA, effect)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            events = await collect(session)
    results = resolutions(events)
    assert len(factories[0].requests) == 2
    assert events[-1].payload.state == ("failure" if first == "caller" else "success")
    if first == "caller":
        assert results[-1].outcome == "cancelled"
        assert results[-1].error.code == "tool_recovery_blocked"
        assert not ledger.exists()
    elif first == "uncertain":
        assert results[0].outcome == "unknown"
        assert len(ledger.read_text().splitlines()) == 1
    else:
        assert results[0].outcome == "failed"
        assert json.loads(results[0].content) == {
            "content": [{"type": "text", "text": "Error executing tool fail"}],
        }
        assert not ledger.exists()
