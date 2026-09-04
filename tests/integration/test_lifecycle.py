import asyncio

import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ApprovalResponse,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.scripted_provider import ScriptedFactory


def provision(monkeypatch, script):
    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory(script)
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    return probe


async def collect(turn):
    return [event async for event in turn.events()]


async def test_raw_run_cancel_abandons_wait_and_close_settles_work(monkeypatch):
    probe = provision(monkeypatch, [{"block": True}])
    async with await create_agent(
        AgentOptions(provider="anthropic", model="claude-sonnet-5")
    ) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            run = asyncio.create_task(session.run(TurnInput([TextPart("Wait")])))
            await asyncio.wait_for(probe.entered.wait(), 5)
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
            assert probe.active == 1
            await session.close()
            assert probe.active == 0
        await session.close()
    await agent.close()


@pytest.mark.parametrize("owner", ["session", "agent"])
async def test_raw_close_cancel_still_awaits_effect_settlement(monkeypatch, owner):
    provision(monkeypatch, [{"tool": {"name": "counter", "arguments": {"value": 7}}}])
    entered, cleaning, release, settled = (asyncio.Event() for _ in range(4))

    async def handler(arguments, context):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            settled.set()

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
    agent = await create_agent(options)
    session = await agent.create_session(SessionOptions(persistence="ephemeral"))
    turn = await session.start_turn(TurnInput([TextPart("Call counter")]))
    events = asyncio.create_task(collect(turn))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        closing = asyncio.create_task((session if owner == "session" else agent).close())
        await asyncio.wait_for(cleaning.wait(), 5)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 5)
        assert settled.is_set()
        assert (await asyncio.wait_for(events, 5))[-1].payload.state == "cancelled"
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await agent.close()


@pytest.mark.parametrize("pending", ["approval", "effect"])
async def test_cancel_settles_caller_work_and_pairs(monkeypatch, pending):
    probe = provision(monkeypatch, [{"tool": {"name": "counter", "arguments": {"value": 7}}}])
    entered, settled = asyncio.Event(), asyncio.Event()
    effects = []

    async def handler(arguments, context):
        effects.append(context.call_id)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    async def approve(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()
        return ApprovalResponse("allow")

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
        approvals=approve if pending == "approval" else "allow",
    )
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Call counter")]))
            collecting = asyncio.create_task(collect(turn))
            await asyncio.wait_for(entered.wait(), 5)
            await turn.cancel()
            assert settled.is_set()
            events = await asyncio.wait_for(collecting, 5)
            assert events[-1].payload.state == "cancelled"
            types = [event.type for event in events]
            assert types.count("tool_call") == types.count("tool_result")
            assert types.count("approval_request") == types.count("approval_decision")
            assert len(effects) == (0 if pending == "approval" else 1)
            assert probe.active == 0


async def test_paused_events_do_not_block_cancellation_or_close(monkeypatch):
    probe = provision(monkeypatch, [{"chunks": ["part"] * 300, "block": True}])
    async with await create_agent(
        AgentOptions(provider="anthropic", model="claude-sonnet-5")
    ) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        turn = await session.start_turn(TurnInput([TextPart("Wait")]))
        await asyncio.wait_for(probe.entered.wait(), 5)
        await asyncio.wait_for(turn.cancel(), 5)
        await asyncio.wait_for(session.close(), 5)
        events = await asyncio.wait_for(collect(turn), 5)
        assert events[-1].payload.state == "cancelled"
        assert probe.active == 0


async def test_event_stream_has_one_consumer(monkeypatch):
    provision(monkeypatch, [{"chunks": ["Hello"], "text": "Hello"}])
    async with await create_agent(
        AgentOptions(provider="anthropic", model="claude-sonnet-5")
    ) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Hello")]))
            await collect(turn)
            with pytest.raises(AgentError, match=".") as error:
                await collect(turn)
            assert error.value.code == "stream_already_consumed"
