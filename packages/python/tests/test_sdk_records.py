import dataclasses

import amplifier_agent as sdk
import pytest
from amplifier_agent_engine import _records as engine
from amplifier_agent_engine._engine import assembly

from conformance.fixtures.scripted_provider import ScriptedFactory


def assert_public(value):
    if dataclasses.is_dataclass(value) or isinstance(value, (sdk.AgentError, engine.AgentError)):
        assert type(value).__module__ == "amplifier_agent._records", type(value)
        assert type(value) is getattr(sdk, type(value).__name__)
        for member in vars(value).values():
            assert_public(member)
    elif isinstance(value, dict):
        for member in value.values():
            assert_public(member)
    elif isinstance(value, (list, tuple)):
        for member in value:
            assert_public(member)


def test_sdk_records_are_distinct_from_engine_values():
    for name in sdk.__all__:
        value = getattr(sdk, name)
        if dataclasses.is_dataclass(value) or name in {
            "AgentError",
            "ToolFailed",
            "ToolOutcomeUnknown",
        }:
            assert value is not getattr(engine, name), name
            assert value.__module__ == "amplifier_agent._records"


async def test_public_records_cross_in_process_engine_and_callback_boundaries(monkeypatch):
    probe = ScriptedFactory(
        [
            {"tool": {"name": "counter", "arguments": {"value": 7}}},
            {"chunks": ["Converted", " records"], "text": "Converted records"},
        ]
    )
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    seen = []

    async def handler(arguments, context):
        assert type(context) is sdk.ToolContext
        assert_public(context)
        seen.append("tool")
        return str(arguments["value"])

    async def approve(request):
        assert type(request) is sdk.ApprovalRequest
        assert_public(request)
        seen.append("approval")
        return sdk.ApprovalResponse("allow")

    options = sdk.AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        approvals=approve,
        tools=[
            sdk.Tool(
                "counter",
                "Record a value",
                {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
                handler,
            )
        ],
    )
    turn_input = sdk.TurnInput(
        [sdk.TextPart("Run counter")],
        history=[sdk.ConversationMessage("user", [sdk.TextPart("Earlier context")])],
    )
    async with await sdk.create_agent(options) as agent:
        async with await agent.create_session(
            sdk.SessionOptions(persistence="ephemeral")
        ) as session:
            assert type(session.info) is sdk.SessionRecord
            assert_public(session.info)
            turn = await session.start_turn(turn_input)
            assert type(turn.info) is sdk.TurnInfo
            assert_public(turn.info)
            events = [event async for event in turn.events()]
            assert_public(events)
            assert events[-1].payload.state == "success"
            history = session.history
            assert_public(history)
            assert history[-1].input == turn_input
            assert history[-1].result == events[-1].payload
    assert seen == ["approval", "tool"]
    assert {event.type for event in events} >= {
        "turn_started",
        "approval_request",
        "approval_decision",
        "tool_call",
        "tool_result",
        "output_delta",
        "usage",
        "terminal",
    }


async def test_engine_refusal_and_terminal_errors_are_sdk_errors(monkeypatch):
    monkeypatch.setattr(assembly, "_provider_factory", ScriptedFactory([{"failure": True}]))
    async with await sdk.create_agent(
        sdk.AgentOptions(provider="anthropic", model="claude-sonnet-5")
    ) as agent:
        async with await agent.create_session(
            sdk.SessionOptions(persistence="ephemeral")
        ) as session:
            with pytest.raises(sdk.AgentError) as refusal:
                await session.start_turn(sdk.TurnInput([]))
            assert_public(refusal.value)
            result = await session.run(sdk.TurnInput([sdk.TextPart("Fail")]))
            assert result.state == "failure"
            assert type(result.error) is sdk.AgentError
            assert_public(result)


@pytest.mark.parametrize("policy", ["retry", None, True, 1, [], {}])
async def test_tool_error_policy_refuses_invalid_values_before_provider_work(policy, monkeypatch):
    probe = ScriptedFactory()
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    with pytest.raises(sdk.AgentError) as refusal:
        await sdk.create_agent(sdk.AgentOptions(tool_error_policy=policy))
    assert refusal.value.code == "invalid_input"
    assert refusal.value.details == {"field": "tool_error_policy"}
    assert "stop" in refusal.value.remedy and "continue" in refusal.value.remedy
    assert probe.requests == []


@pytest.mark.parametrize("policy,expected", [("stop", "failure"), ("continue", "success")])
async def test_tool_error_policy_is_snapshotted_through_python_binding(policy, expected, monkeypatch):
    probe = ScriptedFactory([
        {"tool": {"name": "counter", "arguments": {}}},
        {"chunks": ["Failure acknowledged"], "text": "Failure acknowledged"},
    ])
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    calls = []

    async def handler(arguments, context):
        calls.append(context.call_id)
        raise sdk.ToolFailed("The counter rejected the operation.")

    options = sdk.AgentOptions(
        approvals="allow", tool_error_policy=policy,
        tools=[sdk.Tool("counter", "Record a value", {
            "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        }, handler)],
    )
    async with await sdk.create_agent(options) as agent:
        options.tool_error_policy = "continue" if policy == "stop" else "stop"
        async with await agent.create_session(sdk.SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(sdk.TurnInput([sdk.TextPart("Call counter")]))
            events = [event async for event in turn.events()]
            assert events[-1].payload.state == expected
            resolutions = [event.payload.resolution for event in events if event.type == "tool_result"]
            assert len(resolutions) == len(calls) == 1
            assert resolutions[0].call_id == calls[0]
            assert resolutions[0].outcome == "failed"
            assert resolutions[0].error.code == "tool_failed"
            assert_public(resolutions)
            assert len(probe.requests) == (1 if policy == "stop" else 2)


@pytest.mark.parametrize("ambient", ["file", "environment"])
async def test_tool_error_policy_has_no_ambient_configuration(ambient, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{"tool_error_policy":"continue"}' if ambient == "file" else "{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(config))
    if ambient == "environment":
        monkeypatch.setenv("AMPLIFIER_AGENT_TOOL_ERROR_POLICY", "continue")
    with pytest.raises(sdk.AgentError) as refusal:
        await sdk.create_agent(sdk.AgentOptions(tool_error_policy="continue"))
    assert refusal.value.code == "invalid_input"
    assert "unregistered" in refusal.value.message
    assert refusal.value.remedy
