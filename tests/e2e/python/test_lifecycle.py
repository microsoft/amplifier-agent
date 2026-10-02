import asyncio

from amplifier_agent import AgentError, AgentOptions, SessionOptions, TextPart, Tool, TurnInput, create_agent
import pytest

from tests.support.engine import provision


async def collect(turn):
    return [event async for event in turn.events()]


async def test_raw_run_cancel_abandons_wait_and_close_settles_work(monkeypatch):
    probe = provision(monkeypatch, [{"block": True}])
    async with await create_agent(AgentOptions(provider="anthropic", model="claude-sonnet-5")) as agent:
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


async def test_paused_events_do_not_block_cancellation_or_close(monkeypatch):
    probe = provision(monkeypatch, [{"chunks": ["part"] * 300, "block": True}])
    async with await create_agent(AgentOptions(provider="anthropic", model="claude-sonnet-5")) as agent:
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
    async with (
        await create_agent(AgentOptions(provider="anthropic", model="claude-sonnet-5")) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("Hello")]))
        await collect(turn)
        with pytest.raises(AgentError) as error:
            await collect(turn)
        assert error.value.code == "stream_already_consumed"
