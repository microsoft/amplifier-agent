"""session.set_model through the public Python API: switching, resume, fork, and admission."""

from amplifier_agent import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
import pytest

from tests.support.engine import provision

PROMPT = TurnInput([TextPart("Reply")])


def options(sessions_directory, **values):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", sessions_directory=sessions_directory, **values)


async def started(session, input=PROMPT):
    turn = await session.start_turn(input)
    events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success", events[-1].payload.error
    return events[0].payload


async def test_switch_then_resume_on_the_saved_selection(monkeypatch, tmp_path):
    provision(monkeypatch)
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(session_id="switch-resume"))
        first = await started(session)
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        second = await started(session)
        await session.close()
    async with await create_agent(options(tmp_path)) as agent:
        listed = {record.session_id: record for record in await agent.list_sessions()}
        resumed = await agent.resume_session("switch-resume")
        third = await started(resumed)
        info = resumed.info
    assert (first.primary_actual.provider, first.primary_actual.model) == ("anthropic", "claude-sonnet-5")
    assert (second.primary_actual.provider, second.primary_actual.model) == ("openai", "gpt-6.1-sol")
    assert (listed["switch-resume"].provider, listed["switch-resume"].model) == ("openai", "gpt-6.1-sol")
    assert third.continuation == "resumed"
    assert (third.primary_actual.provider, third.primary_actual.model) == ("openai", "gpt-6.1-sol")
    assert (info.provider, info.model) == ("openai", "gpt-6.1-sol")


async def test_fork_inherits_and_then_switches_independently(monkeypatch, tmp_path):
    provision(monkeypatch)
    async with await create_agent(options(tmp_path)) as agent:
        parent = await agent.create_session(SessionOptions(persistence="ephemeral"))
        await started(parent)
        await parent.set_model(provider="openai", model="gpt-6.1-sol")
        child = await parent.fork()
        inherited = child.info
        await child.set_model(provider="gemini", model="gemini-3.8-flash")
        child_turn = await started(child)
        parent_turn = await started(parent)
    assert (inherited.provider, inherited.model) == ("openai", "gpt-6.1-sol")
    assert (child_turn.primary_actual.provider, child_turn.primary_actual.model) == ("gemini", "gemini-3.8-flash")
    assert (parent_turn.primary_actual.provider, parent_turn.primary_actual.model) == ("openai", "gpt-6.1-sol")


async def test_set_model_waits_for_the_active_turn(monkeypatch, tmp_path):
    factory = provision(monkeypatch, [{"block": True}])
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        turn = await session.start_turn(PROMPT)
        await factory.entered.wait()
        with pytest.raises(AgentError) as caught:
            await session.set_model(provider="openai", model="gpt-6.1-sol")
        await turn.cancel()
        _ = [event async for event in turn.events()]
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        info = session.info
    assert caught.value.code == "busy"
    assert caught.value.remedy
    assert (info.provider, info.model) == ("openai", "gpt-6.1-sol")
