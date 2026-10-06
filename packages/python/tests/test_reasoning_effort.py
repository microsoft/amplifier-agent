"""The Python binding carries reasoning_effort across the engine boundary in both directions."""

import dataclasses
import os

import amplifier_agent as sdk
import pytest

from tests.support.engine import provision


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return path


def options(**fields):
    return sdk.AgentOptions(provider="anthropic", model="claude-sonnet-5", **fields)


@pytest.mark.parametrize("record", [sdk.AgentOptions, sdk.SessionOptions, sdk.TurnInput, sdk.TurnStarted])
def test_public_records_declare_an_optional_reasoning_effort(record):
    field = {item.name: item for item in dataclasses.fields(record)}["reasoning_effort"]
    assert field.default is None


async def test_values_cross_the_engine_boundary_as_public_records(monkeypatch):
    probe = provision(monkeypatch, [{"text": "Reply"}])
    turn_input = sdk.TurnInput([sdk.TextPart("Reply")], reasoning_effort="low")
    async with (
        await sdk.create_agent(options(reasoning_effort="high")) as agent,
        await agent.create_session(sdk.SessionOptions(persistence="ephemeral", reasoning_effort="medium")) as session,
    ):
        turn = await session.start_turn(turn_input)
        events = [event async for event in turn.events()]
        history = session.history
    started = events[0].payload
    assert type(started) is sdk.TurnStarted
    assert started.reasoning_effort == "low"
    assert probe.requests[-1]["reasoning_effort"] == "low"
    assert history[-1].input == turn_input
    assert type(history[-1].input) is sdk.TurnInput


async def test_absent_values_report_the_default(monkeypatch):
    provision(monkeypatch, [{"text": "Reply"}])
    async with (
        await sdk.create_agent(options()) as agent,
        await agent.create_session(sdk.SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(sdk.TurnInput([sdk.TextPart("Reply")]))
        events = [event async for event in turn.events()]
    assert events[0].payload.reasoning_effort == "medium"


@pytest.mark.parametrize("value", ["High", "", 3])
async def test_unregistered_values_are_public_invalid_input_at_each_method(monkeypatch, value):
    probe = provision(monkeypatch, [{"text": "Reply"}])
    with pytest.raises(sdk.AgentError) as at_agent:
        await sdk.create_agent(options(reasoning_effort=value))
    async with await sdk.create_agent(options()) as agent:
        with pytest.raises(sdk.AgentError) as at_session:
            await agent.create_session(sdk.SessionOptions(persistence="ephemeral", reasoning_effort=value))
        async with await agent.create_session(sdk.SessionOptions(persistence="ephemeral")) as session:
            with pytest.raises(sdk.AgentError) as at_turn:
                await session.start_turn(sdk.TurnInput([sdk.TextPart("Reply")], reasoning_effort=value))
    for caught in (at_agent, at_session, at_turn):
        assert type(caught.value) is sdk.AgentError
        assert caught.value.code == "invalid_input"
        assert "reasoning_effort" in caught.value.message
    assert probe.requests == []


async def test_values_above_the_agent_value_are_honored(monkeypatch):
    probe = provision(monkeypatch, [{"text": "Reply"}, {"text": "Reply"}])
    async with await sdk.create_agent(options(reasoning_effort="low")) as agent:
        async with await agent.create_session(
            sdk.SessionOptions(persistence="ephemeral", reasoning_effort="high")
        ) as session:
            at_session = await session.run(sdk.TurnInput([sdk.TextPart("Reply")]))
        async with await agent.create_session(sdk.SessionOptions(persistence="ephemeral")) as session:
            at_turn = await session.run(sdk.TurnInput([sdk.TextPart("Reply")], reasoning_effort="medium"))
    assert (at_session.state, at_turn.state) == ("success", "success")
    assert [request["reasoning_effort"] for request in probe.requests] == ["high", "medium"]


def test_existing_positional_calls_keep_their_meaning():
    history = [sdk.ConversationMessage("user", [sdk.TextPart("Earlier")])]
    agent = sdk.AgentOptions("anthropic", "claude-sonnet-5", "Instructions")
    turn = sdk.TurnInput([sdk.TextPart("Reply")], "claude-sonnet-5", history)
    assert (agent.provider, agent.model, agent.instructions) == ("anthropic", "claude-sonnet-5", "Instructions")
    assert (turn.model, turn.history) == ("claude-sonnet-5", history)
    assert agent.reasoning_effort is None
    assert turn.reasoning_effort is None


async def test_model_records_carry_reasoning_efforts_as_public_records(monkeypatch):
    from amplifier_agent_engine._engine import discovery
    from amplifier_core.models import ModelInfo

    assert [item.name for item in dataclasses.fields(sdk.ModelRecord)][-1] == "reasoning_efforts"

    async def listed(provider, instance):
        return [
            ModelInfo(id=model, display_name=model, context_window=1, max_output_tokens=1)
            for model in ("gpt-5.5-pro", "gpt-4.1")
        ]

    monkeypatch.setattr(discovery, "_models", listed)
    records = await sdk.list_models("openai", sdk.DiscoveryOptions(environment={"OPENAI_API_KEY": "fixture-key"}))
    assert all(type(record) is sdk.ModelRecord for record in records)
    assert [record.reasoning_efforts for record in records] == [["medium", "high", "xhigh"], []]
