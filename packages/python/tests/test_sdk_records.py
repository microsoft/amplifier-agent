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
