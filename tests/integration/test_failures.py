import json

import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ConversationMessage,
    SessionOptions,
    TextPart,
    Tool,
    ToolFailed,
    ToolOutcomeUnknown,
    TurnInput,
    create_agent,
)

from conformance.fixtures.scripted_provider import ScriptedFactory


@pytest.mark.parametrize(
    "failure,code,outcome",
    [
        (RuntimeError("callback died"), "tool_callback_failed", "unknown"),
        (ToolFailed("effect failed"), "tool_failed", "failed"),
        (ToolOutcomeUnknown("effect uncertain"), "tool_completion_unknown", "unknown"),
        (None, "tool_result_invalid", "unknown"),
    ],
)
async def test_callback_failures_preserve_resolution_without_retry(
    monkeypatch, failure, code, outcome
):
    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory(
        [
            {"tool": {"name": "counter", "arguments": {"value": 7}}},
            {"chunks": ["Recovered"], "text": "Recovered"},
        ]
    )
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    calls = []

    async def handler(arguments, context):
        calls.append(context.call_id)
        if failure is not None:
            raise failure
        return {"invalid": "result"}

    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        approvals="allow",
        tools=[
            Tool(
                "counter",
                "Record a value",
                {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
                handler,
            ),
        ],
    )
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Call counter")]))
            events = [event async for event in turn.events()]
            assert len(probe.requests) == 1
            followup = await session.run(TurnInput([TextPart("Continue")]))
            assert followup.state == "success"
    terminal = events[-1].payload
    assert terminal.state == "failure"
    assert terminal.error.code == code
    assert terminal.error.remedy
    resolutions = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert len(resolutions) == len(calls) == 1
    assert len(probe.requests) == 2
    assert resolutions[0].outcome == outcome
    assert resolutions[0].call_id == calls[0]
    assert resolutions[0].error.code == code
    tool_messages = [
        message for message in probe.requests[-1]["messages"] if message["role"] == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == calls[0]
    assert json.loads(tool_messages[0]["content"])["outcome"] == outcome


async def test_missing_authority_refuses_effect(monkeypatch):
    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory([{"tool": {"name": "counter", "arguments": {"value": 7}}}])
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    calls = []

    async def handler(arguments, context):
        calls.append(context.call_id)
        return "7"

    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        tools=[
            Tool(
                "counter",
                "Record a value",
                {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
                handler,
            )
        ],
    )
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            result = await session.run(TurnInput([TextPart("Call counter")]))
    assert result.state == "failure"
    assert result.error.code == "approval_unavailable"
    assert calls == []


async def test_invalid_seed_refusal_is_atomic(monkeypatch):
    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory([{"chunks": ["Hello"], "text": "Hello"}])
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    async with await create_agent(
        AgentOptions(provider="anthropic", model="claude-sonnet-5")
    ) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            with pytest.raises(AgentError) as error:
                await session.start_turn(
                    TurnInput([], history=[ConversationMessage("tool", [TextPart("Invalid")])])
                )
            assert error.value.code == "invalid_input"
            assert error.value.remedy
            assert probe.requests == []
            assert session.history == []
            result = await session.run(
                TurnInput(
                    [], history=[ConversationMessage("developer", [TextPart("Valid history")])]
                )
            )
            assert result.state == "success"
            assert len(probe.requests) == 1
            assert probe.requests[0]["messages"][-1]["role"] == "developer"
