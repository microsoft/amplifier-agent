import asyncio
import json

import pytest
import yaml
from amplifier_agent import (
    BUILTIN_TOOLS,
    AgentError,
    AgentOptions,
    ApprovalResponse,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.engine import provision_many as provision

SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def write_skill(root, *, hooks=None, body="Perform the requested work.", **metadata):
    root.mkdir(parents=True, exist_ok=True)
    header = {"name": "review", "description": "Review the supplied work.", **metadata}
    if hooks is not None:
        header["hooks"] = hooks
    (root / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\n" + body)
    return root


def command(text, **extra):
    return {"hooks": [{"type": "command", "command": text, **extra}]}


def invoke(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


async def start(session):
    return await session.start_turn(TurnInput([TextPart("Review this work.")]))


async def events(session):
    turn = await start(session)
    return [event async for event in turn.events()]


def options(skill, **extra):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", skills=[str(skill)],
                        **{"approvals": "allow", **extra})


def paired(stream):
    calls = [event.payload.call for event in stream if event.type == "tool_call"]
    results = [event.payload.resolution for event in stream if event.type == "tool_result"]
    assert len(calls) == len(results) == len({call.call_id for call in calls})
    assert {call.call_id for call in calls} == {result.call_id for result in results}
    assert [event.sequence for event in stream] == list(range(1, len(stream) + 1))
    return calls, results


@pytest.mark.parametrize("shell_form", [False, True])
@pytest.mark.production_only
async def test_inline_hooks_receive_input_context_and_expire_at_turn_end(monkeypatch, tmp_path, shell_form):
    script = tmp_path / "gate.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "value = json.load(sys.stdin)\n"
        "with pathlib.Path('ledger').open('a') as f: f.write(json.dumps(value) + '\\n')\n"
        "print(json.dumps({'hookSpecificOutput': {'additionalContext': 'hook context marker'}}))\n"
        "assert os.environ['AMPLIFIER_SKILL_DIR'] == str(pathlib.Path.cwd())\n"
    )
    hooks = {}
    for name, alias in [("PreToolUse", "pre-tool"), ("PostToolUse", "post-tool"), ("Stop", "stop")]:
        if shell_form:
            hooks.setdefault("shell", []).append({"event": alias, "command": 'python3 "$AMPLIFIER_SKILL_DIR/gate.py"',
                                                   **({"matcher": "counter"} if name != "Stop" else {})})
        else:
            hooks[name] = [{**command('python3 "$AMPLIFIER_SKILL_DIR/gate.py"'),
                            **({"matcher": "counter"} if name != "Stop" else {})}]
    skill = write_skill(tmp_path, hooks=hooks)
    factories = provision(monkeypatch, [invoke("load_skill", name="review"), invoke("counter", marker="$(touch injected)"),
                                        {"text": "First complete"}, invoke("counter", marker="later"), {"text": "Second complete"}])
    calls = []

    async def counter(arguments, context):
        calls.append(arguments)
        return "counted"

    tool = Tool("counter", "Record a marker.", {"$schema": SCHEMA, "type": "object"}, counter)
    async with await create_agent(options(skill, tools=[*BUILTIN_TOOLS, tool])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            first, second = await events(session), await events(session)
    assert first[-1].payload.state == second[-1].payload.state == "success"
    first_calls, _ = paired(first)
    assert [call.name for call in first_calls] == ["load_skill", "bash", "counter", "bash", "bash"]
    assert [call.name for call in paired(second)[0]] == ["counter"]
    ledger = [json.loads(line) for line in (tmp_path / "ledger").read_text().splitlines()]
    assert [item["hook_event_name"] for item in ledger] == ["PreToolUse", "PostToolUse", "Stop"]
    assert ledger[0]["tool_input"] == {"marker": "$(touch injected)"}
    assert ledger[1]["tool_response"] == "counted"
    assert not (tmp_path / "injected").exists()
    assert "hook context marker" in str(factories[0].requests[2]["messages"])
    assert "hook context marker" not in str(factories[0].requests[3]["messages"])
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["deny", "nonzero", "block", "malformed"])
async def test_hook_refusal_prevents_target_effect_and_resolves_pairs(monkeypatch, tmp_path, failure):
    shell = {"deny": "printf allowed > hook-ran", "nonzero": "exit 7",
             "block": "printf '%s' '{\"decision\":\"block\",\"reason\":\"Gate refused\"}'",
             "malformed": "printf '%s' '{\"continue\":\"yes\"}'"}[failure]
    skill = write_skill(tmp_path, hooks={"PreToolUse": [{**command(shell), "matcher": "counter"}]})
    provision(monkeypatch, [invoke("load_skill", name="review"), invoke("counter")])
    effects = []

    async def counter(arguments, context):
        effects.append(True)
        return "unreachable"

    async def approval(request):
        return ApprovalResponse("deny" if failure == "deny" and request.name == "bash" else "allow")

    tool = Tool("counter", "Record an effect.", {"$schema": SCHEMA, "type": "object"}, counter)
    async with await create_agent(options(skill, approvals=approval, tools=[*BUILTIN_TOOLS, tool])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert not effects
    assert stream[-1].payload.state == ("rejected" if failure == "deny" else "failure")
    assert stream[-1].payload.error.code == ("approval_denied" if failure == "deny" else "tool_failed")
    calls, _ = paired(stream)
    assert [call.name for call in calls] == ["load_skill", "bash"]
    assert not (tmp_path / "hook-ran").exists()


async def test_hook_cancellation_drains_process_and_removes_hook_scope(monkeypatch, tmp_path):
    skill = write_skill(tmp_path, hooks={"PreToolUse": [{
        **command("printf started > started; sleep 20; printf late > late", timeout=30), "matcher": "counter",
    }]})
    provision(monkeypatch, [invoke("load_skill", name="review"), invoke("counter"), {"text": "Next turn"}])

    async def counter(arguments, context):
        raise AssertionError("Cancelled hook must not start its target effect")

    tool = Tool("counter", "Record an effect.", {"$schema": SCHEMA, "type": "object"}, counter)
    async with await create_agent(options(skill, tools=[*BUILTIN_TOOLS, tool])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await start(session)

            async def collect():
                return [event async for event in turn.events()]

            collecting = asyncio.create_task(collect())
            async with asyncio.timeout(5):
                while not (tmp_path / "started").exists():
                    await asyncio.sleep(0.01)
            await turn.cancel()
            stream = await collecting
            later = await events(session)
    assert stream[-1].payload.state == "cancelled"
    calls, results = paired(stream)
    assert [call.name for call in calls] == ["load_skill", "bash"]
    assert results[-1].outcome == "unknown"
    assert not (tmp_path / "late").exists()
    assert later[-1].payload.state == "success"
    assert not paired(later)[0]


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.production_only
async def test_named_agent_preserves_instructions_tools_ceiling_and_child_hook_scope(monkeypatch, tmp_path, qualified):
    (tmp_path / "bundle.md").write_text("---\nbundle:\n  name: workshop\n---\n")
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "reviewer.md").write_text(
        "---\nmeta:\n  name: reviewer\nmodel: claude-sonnet-5\n"
        "tools: [counter, bash]\nagents: none\n---\nNamed reviewer instructions.\n"
    )
    skill = write_skill(tmp_path / "skills" / "review", context="fork",
                        agent="workshop:reviewer" if qualified else "reviewer",
                        hooks={"PreToolUse": [{**command("printf child >> ledger"), "matcher": "counter"}],
                               "Stop": [command("printf stopped >> ledger")]})
    factories = provision(monkeypatch,
                          [invoke("load_skill", name="review"), invoke("counter"), {"text": "Parent complete"}],
                          [invoke("counter"), {"text": "Child complete"}])
    effects = []

    async def counter(arguments, context):
        effects.append(context.call_id)
        return "counted"

    tool = Tool("counter", "Record an effect.", {"$schema": SCHEMA, "type": "object"}, counter)
    config = AgentOptions(provider="anthropic", model="claude-opus-5", instructions="Host instructions.",
                          approvals="allow", skills=[str(skill.parent)], tools=[*BUILTIN_TOOLS, tool])
    async with await create_agent(config) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert stream[-1].payload.state == "success"
    assert (skill / "ledger").read_text() == "childstopped"
    child = factories[1].requests[0]
    assert "Host instructions." in str(child["messages"])
    assert "Named reviewer instructions." in str(child["messages"])
    assert factories[1].selected_models == ["claude-sonnet-5", "claude-sonnet-5"]
    assert {entry["name"] for entry in child["tools"]} == {"counter", "bash"}
    assert len(effects) == 2 and len(set(effects)) == 2
    assert {entry.model for entry in stream[-1].payload.usage.entries} == {"claude-sonnet-5", "claude-opus-5"}
    paired(stream)


@pytest.mark.parametrize("restriction,code", [("tools: [counter]", "invalid_input"),
                                               ("model: claude-opus-5", "selector_rejected")])
@pytest.mark.production_only
async def test_named_agent_restrictions_are_checked_before_skill_preprocessing(monkeypatch, tmp_path, restriction, code):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "reviewer.md").write_text(
        "---\nmeta:\n  name: reviewer\n" + restriction + "\n---\nReview carefully.\n"
    )
    write_skill(tmp_path / "skills" / "review", context="fork", agent="reviewer",
                body="!`printf forbidden > effect`\nReview.")
    provision(monkeypatch, [invoke("load_skill", name="review")])

    async def counter(arguments, context):
        return "counted"

    tool = Tool("counter", "Record an effect.", {"$schema": SCHEMA, "type": "object"}, counter)
    async with await create_agent(options(tmp_path, tools=[*BUILTIN_TOOLS, tool])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert stream[-1].payload.error.code == code
    assert [call.name for call in paired(stream)[0]] == ["load_skill"]
    assert not (tmp_path / "skills" / "review" / "effect").exists()


@pytest.mark.production_only
async def test_empty_named_agent_tools_do_not_expand_when_delegation_is_disabled(monkeypatch, tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ntools: []\nagents: none\n---\nReview without tools.\n"
    )
    write_skill(tmp_path / "skills" / "review", context="fork", agent="reviewer")
    factories = provision(monkeypatch, [invoke("load_skill", name="review"), {"text": "Parent"}], [{"text": "Child"}])
    async with await create_agent(options(tmp_path)) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert stream[-1].payload.state == "success"
    assert not factories[1].requests[0]["tools"]


@pytest.mark.parametrize("hooks", [
    {"SessionStart": []}, {"SessionEnd": [command("printf ignored")]},
    {"PreToolUse": [{"hooks": [{"type": "prompt", "command": "ignored"}]}]},
    {"shell": [{"event": "pre-tool", "command": "printf ignored", "on_failure": "ignore"}]},
])
@pytest.mark.production_only
async def test_unsupported_hook_execution_semantics_are_refused_at_construction(monkeypatch, tmp_path, hooks):
    skill = write_skill(tmp_path, hooks=hooks)
    provision(monkeypatch, [{"text": "unused"}])
    with pytest.raises(AgentError) as error:
        await create_agent(options(skill))
    assert error.value.code == "invalid_input"


@pytest.mark.production_only
async def test_auto_loaded_hooks_execute_after_turn_admission_and_remain_approved(monkeypatch, tmp_path):
    skill = write_skill(tmp_path, hooks={"PreToolUse": [{**command("printf gate >> ledger"), "matcher": "bash"}]},
                        **{"auto-load": True})
    provision(monkeypatch, [invoke("bash", command="printf target"), {"text": "Done"}])
    async with await create_agent(options(skill)) as agent:
        assert not (skill / "ledger").exists()
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            assert not (skill / "ledger").exists()
            stream = await events(session)
    assert stream[0].type == "turn_started" and stream[-1].payload.state == "success"
    assert (skill / "ledger").read_text() == "gate"
    assert [call.name for call in paired(stream)[0]] == ["bash", "bash"]


@pytest.mark.parametrize("metadata", [
    {"auto-load": "false"}, {"auto-load": True, "allowed-tools": ["read_file"]},
    {"auto-load": True, "context": "fork"},
])
@pytest.mark.production_only
async def test_automatic_hooks_do_not_bypass_skill_authority(monkeypatch, tmp_path, metadata):
    skill = write_skill(tmp_path, hooks={"Stop": [command("printf forbidden > effect")]}, **metadata)
    provision(monkeypatch, [{"text": "Unused"}])
    with pytest.raises(AgentError) as error:
        await create_agent(options(skill))
    assert error.value.code == "invalid_input"
    assert not (tmp_path / "effect").exists()


@pytest.mark.production_only
async def test_skill_file_symlink_cannot_escape_configured_source(monkeypatch, tmp_path):
    outside = write_skill(tmp_path / "outside")
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").symlink_to(outside / "SKILL.md")
    provision(monkeypatch, [{"text": "Unused"}])
    with pytest.raises(AgentError) as error:
        await create_agent(options(source))
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize("result", [
    '{"decision":"block","decision":"approve"}',
    '{"hookSpecificOutput":{"hookEventName":"Stop"}}',
    '{"suppressOutput":"yes"}', '{"continue":NaN}',
])
@pytest.mark.production_only
async def test_structured_hook_results_are_strict_and_correlated(monkeypatch, tmp_path, result):
    import shlex

    skill = write_skill(tmp_path, hooks={"PreToolUse": [
        {**command("printf %s " + shlex.quote(result)), "matcher": "bash"},
    ]})
    provision(monkeypatch, [invoke("load_skill", name="review"), invoke("bash", command="printf forbidden > effect")])
    async with await create_agent(options(skill)) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert stream[-1].payload.error.code == "tool_failed"
    assert not (tmp_path / "effect").exists()
    assert len(paired(stream)[0]) == 2


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.production_only
async def test_inline_skill_tool_restrictions_expire_without_widening_delegation(monkeypatch, tmp_path, automatic):
    extra = {"auto-load": automatic, "allowed-tools": ["bash"]}
    hooks = {"Stop": [command("printf stopped")]} if automatic else None
    skill = write_skill(tmp_path, hooks=hooks, **extra)
    script = ([] if automatic else [invoke("load_skill", name="review")]) + [
        invoke("write_file", file_path=str(tmp_path / "forbidden"), content="forbidden"),
    ]
    factories = provision(monkeypatch, script)
    async with await create_agent(options(skill)) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert stream[-1].payload.state == "failure"
    assert stream[-1].payload.error.code == "provider_failed"
    assert not (tmp_path / "forbidden").exists()
    assert {tool["name"] for tool in factories[0].requests[-1]["tools"]} == {"bash"}
    assert not any(call.name == "write_file" for call in paired(stream)[0])


@pytest.mark.parametrize("block", [False, True])
async def test_active_hooks_guard_later_skill_preprocessing(monkeypatch, tmp_path, block):
    gate = "printf '%s' '{\"decision\":\"block\"}'" if block else "printf guard >> ledger"
    write_skill(tmp_path / "guard", name="guard", hooks={
        "PreToolUse": [{**command(gate), "matcher": "bash"}],
        "PostToolUse": [{**command("printf post >> ledger"), "matcher": "bash"}],
    })
    skill = write_skill(tmp_path / "review", body="!`printf performed > effect`\nReview this work.")
    provision(monkeypatch, [invoke("load_skill", name="guard"), invoke("load_skill", name="review"),
                            {"text": "Done"}])
    async with await create_agent(options(tmp_path)) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            stream = await events(session)
    assert stream[-1].payload.state == ("failure" if block else "success")
    assert (skill / "effect").exists() == (not block)
    calls, _ = paired(stream)
    assert [call.name for call in calls] == ["load_skill", "load_skill"] + ["bash"] * (1 if block else 3)
    if block:
        assert stream[-1].payload.error.code == "tool_failed"
    else:
        assert (tmp_path / "guard" / "ledger").read_text() == "guardpost"


@pytest.mark.parametrize("extra", [
    {1: "invalid"}, {"agent": []}, {"agent": {}},
    {"allowed-tools": [{"module": []}]}, {"allowed-tools": [{"module": {}}]},
    {"hooks": {"shell": [{"event": [], "command": "printf forbidden"}]}},
    {"hooks": {"shell": [{"event": {}, "command": "printf forbidden"}]}},
    {"hooks": {1: []}},
])
@pytest.mark.production_only
async def test_malformed_skill_declarations_have_named_input_errors(monkeypatch, tmp_path, extra):
    header = {"name": "review", "description": "Review the task.", **extra}
    (tmp_path / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\nReview carefully.\n")
    provision(monkeypatch, [{"text": "Unused"}])
    with pytest.raises(AgentError) as error:
        await create_agent(options(tmp_path))
    assert error.value.code == "invalid_input"
