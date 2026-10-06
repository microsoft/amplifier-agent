"""The Python binding carries session.set_model and the session's selection across the engine boundary."""

import dataclasses
import inspect
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


def test_set_model_is_an_async_session_method():
    method = sdk.Session.set_model
    assert inspect.iscoroutinefunction(method)
    parameters = inspect.signature(method).parameters
    assert list(parameters) == ["self", "provider", "model", "reasoning_effort"]
    assert parameters["reasoning_effort"].default is None


def test_session_record_declares_the_selection():
    fields = {item.name: item for item in dataclasses.fields(sdk.SessionRecord)}
    assert list(fields) == ["session_id", "persistence", "provider", "model", "reasoning_effort"]
    assert fields["reasoning_effort"].default is None


async def test_selection_crosses_as_public_records(monkeypatch):
    provision(monkeypatch, [{"text": "Reply"}, {"text": "Reply"}])
    async with (
        await sdk.create_agent(options()) as agent,
        await agent.create_session(sdk.SessionOptions(persistence="ephemeral")) as session,
    ):
        before = session.info
        await session.set_model(provider="openai", model="gpt-6-sol", reasoning_effort="low")
        after = session.info
        turn = await session.start_turn(sdk.TurnInput([sdk.TextPart("Reply")]))
        events = [event async for event in turn.events()]
    assert type(before) is sdk.SessionRecord
    assert type(after) is sdk.SessionRecord
    assert (before.provider, before.model) == ("anthropic", "claude-sonnet-5")
    assert (after.provider, after.model, after.reasoning_effort) == ("openai", "gpt-6-sol", "low")
    started = events[0].payload
    assert type(started) is sdk.TurnStarted
    assert (started.primary_actual.provider, started.primary_actual.model) == ("openai", "gpt-6-sol")
    assert started.reasoning_effort == "low"


async def test_listed_sessions_carry_the_selection(monkeypatch, tmp_path):
    provision(monkeypatch, [{"text": "Reply"}])
    async with await sdk.create_agent(options(sessions_directory=tmp_path)) as agent:
        async with await agent.create_session(sdk.SessionOptions(session_id="listed-session")) as session:
            await session.set_model(provider="openai", model="gpt-6-sol")
            await session.run(sdk.TurnInput([sdk.TextPart("Reply")]))
        (record,) = await agent.list_sessions()
    assert type(record) is sdk.SessionRecord
    assert (record.session_id, record.provider, record.model) == ("listed-session", "openai", "gpt-6-sol")


async def test_errors_cross_as_public_agent_errors(monkeypatch):
    probe = provision(monkeypatch, [{"text": "Reply"}])
    async with await sdk.create_agent(options()) as agent:
        session = await agent.create_session(sdk.SessionOptions(persistence="ephemeral"))
        with pytest.raises(sdk.AgentError) as unknown:
            await session.set_model(provider="not-a-provider", model="x")
        await session.close()
        with pytest.raises(sdk.AgentError) as closed:
            await session.set_model(provider="openai", model="gpt-6-sol")
    for caught, code in ((unknown, "invalid_input"), (closed, "closed")):
        assert type(caught.value) is sdk.AgentError
        assert caught.value.code == code
        assert caught.value.remedy
    assert probe.requests == []
