import pytest
import yaml
from amplifier_agent import (
    BUILTIN_TOOLS,
    AgentError,
    AgentOptions,
    SessionOptions,
    TextPart,
    Tool,
    ToolOutcomeUnknown,
    TurnInput,
    create_agent,
)

from conformance.fixtures.engine import provision_many as provision

SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}


def call(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


def options(**kwargs):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow", **kwargs)


def offered(request):
    return sorted(tool["name"] for tool in request.get("tools") or [])


async def run(agent, text="Use the configured tools."):
    async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
        turn = await session.start_turn(TurnInput([TextPart(text)]))
        return [event async for event in turn.events()]


def test_the_binding_exports_the_nine_built_in_names():
    assert BUILTIN_TOOLS == (
        "read_file", "write_file", "edit_file", "glob", "grep", "bash", "web_fetch",
        "web_search", "delegate",
    )


async def test_a_caller_bash_in_a_caller_only_set_executes_in_the_caller(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    probes = provision(monkeypatch, [call("bash", command="printf built-in > effect"), {"text": "Done"}])
    effects = []

    async def bash(arguments, context):
        effects.append(arguments)
        return "caller"

    async with await create_agent(options(tools=[Tool("bash", "Caller shell.", SCHEMA, bash)])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    calls = [event.payload.call for event in events if event.type == "tool_call"]
    assert [(item.name, item.source) for item in calls] == [("bash", "caller")]
    assert effects == [{"command": "printf built-in > effect"}]
    assert not (tmp_path / "effect").exists()
    assert offered(probes[0].requests[0]) == ["bash"]


async def test_a_selected_set_offers_exactly_its_entries(monkeypatch):
    probes = provision(monkeypatch, [{"text": "Listed"}])

    async def counter(arguments, context):
        return "counted"

    tools = ["read_file", Tool("counter", "Count.", SCHEMA, counter)]
    async with await create_agent(options(tools=tools)) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert offered(probes[0].requests[0]) == ["counter", "read_file"]


@pytest.mark.production_only
async def test_an_absent_set_offers_every_built_in(monkeypatch):
    probes = provision(monkeypatch, [{"text": "Listed"}])
    async with await create_agent(options()) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert set(BUILTIN_TOOLS) <= set(offered(probes[0].requests[0]))


async def test_an_empty_set_offers_no_tools(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    probes = provision(monkeypatch, [call("write_file", file_path="effect", content="x")])
    async with await create_agent(options(tools=[])) as agent:
        events = await run(agent)
    assert offered(probes[0].requests[0]) == []
    assert events[-1].payload.state == "failure"
    assert not (tmp_path / "effect").exists()


async def test_an_unknown_built_in_name_is_refused_before_provider_work(monkeypatch):
    probes = provision(monkeypatch, [{"text": "Unreachable"}])
    with pytest.raises(AgentError) as caught:
        await create_agent(options(tools=["read_file", "shell"]))
    assert caught.value.code == "invalid_input"
    assert "BUILTIN_TOOLS" in caught.value.remedy
    assert all(not probe.requests for probe in probes)


async def test_unknown_outcome_without_inspection_tools_permits_only_model_responses(monkeypatch):
    probes = provision(monkeypatch, [call("uncertain"), call("other")])
    effects = []

    async def uncertain(arguments, context):
        effects.append("uncertain")
        raise ToolOutcomeUnknown("The outcome cannot be established.")

    async def other(arguments, context):
        effects.append("other")
        return "must not run"

    tools = ["write_file", Tool("uncertain", "Uncertain.", SCHEMA, uncertain),
             Tool("other", "Other.", SCHEMA, other)]
    async with await create_agent(options(tools=tools, tool_error_policy="continue")) as agent:
        events = await run(agent)
    assert effects == ["uncertain"]
    assert offered(probes[0].requests[0]) == ["other", "uncertain", "write_file"]
    assert offered(probes[0].requests[1]) == []
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == "tool_recovery_blocked"


@pytest.mark.production_only
@pytest.mark.parametrize("tools", [["read_file"], []])
async def test_automatic_skill_commands_require_bash_in_the_set(monkeypatch, tmp_path, tools):
    header = {"name": "review", "description": "Review the supplied work.", "auto-load": True,
              "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "printf x > effect"}]}]}}
    (tmp_path / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\nReview.")
    probes = provision(monkeypatch, [{"text": "Unused"}])
    with pytest.raises(AgentError) as caught:
        await create_agent(options(tools=tools, skills=[str(tmp_path)]))
    assert caught.value.code == "invalid_input"
    assert "bash" in caught.value.remedy
    assert all(not probe.requests for probe in probes)
    async with await create_agent(options(tools=["bash"], skills=[str(tmp_path)])):
        pass
    assert not (tmp_path / "effect").exists()


@pytest.mark.production_only
async def test_loaded_skill_commands_never_run_through_a_caller_bash(monkeypatch, tmp_path):
    header = {"name": "review", "description": "Review the supplied work.",
              "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "printf x > effect"}]}]}}
    (tmp_path / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\nReview.")
    provision(monkeypatch, [call("load_skill", name="review"), call("note"), {"text": "Unreachable"}])
    effects = []

    async def caller(arguments, context):
        effects.append(arguments)
        return "caller"

    tools = [Tool("bash", "Caller shell.", SCHEMA, caller), Tool("note", "Note.", SCHEMA, caller)]
    async with await create_agent(options(tools=tools, skills=[str(tmp_path)])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == "invalid_input"
    assert effects == []
    assert not (tmp_path / "effect").exists()
