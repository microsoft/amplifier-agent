import asyncio

import pytest
from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.journal import EventJournal
from amplifier_agent_engine._records import (
    AgentError,
    AgentOptions,
    SessionOptions,
    TextPart,
    TurnInput,
)
from amplifier_agent_engine._runtime.server import RuntimeServer

from conformance.fixtures.scripted_provider import ScriptedFactory


async def runtime(monkeypatch, script):
    probe = ScriptedFactory(script)
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    server = RuntimeServer()
    messages = []

    async def capture(value):
        messages.append(value)

    monkeypatch.setattr(server, "send", capture)
    await server.dispatch(
        {
            "id": 1,
            "method": "agent.create",
            "params": {"options": {"provider": "anthropic", "model": "claude-sonnet-5"}},
        }
    )
    agent_id = messages[-1]["result"]["agent_id"]
    await server.dispatch(
        {
            "id": 2,
            "method": "agent.create_session",
            "params": {"agent_id": agent_id, "options": {"persistence": "ephemeral"}},
        }
    )
    session_id = messages[-1]["result"]["info"].session_id
    return server, messages, probe, agent_id, session_id


async def test_completed_streams_and_closed_sessions_release_runtime_references(monkeypatch):
    server, messages, _, agent_id, session_id = await runtime(monkeypatch, [{"chunks": ["Hi"]}])
    try:
        await server.dispatch(
            {
                "id": 3,
                "method": "session.start_turn",
                "params": {
                    "session_id": session_id,
                    "input": {"content": [{"type": "text", "text": "Hello"}]},
                },
            }
        )
        turn_id = messages[-1]["result"]["info"].turn_id
        await asyncio.wait_for(asyncio.gather(*server.pumps), 5)
        assert messages[-1]["event_data"].type == "terminal"
        assert not (server.turns or server.windows or server.turn_sessions or server.pump_tasks)
        await server.dispatch({"id": 4, "method": "turn.cancel", "params": {"turn_id": turn_id}})
        assert messages[-1] == {"id": 4, "result": None}
        await server.dispatch(
            {"id": 5, "method": "session.close", "params": {"session_id": session_id}}
        )
        assert not (server.sessions or server.session_agents)
    finally:
        await server.agents[agent_id].close()


async def test_run_close_race_returns_its_terminal_history(monkeypatch):
    server, messages, probe, agent_id, session_id = await runtime(monkeypatch, [{"block": True}])
    running = asyncio.create_task(
        server.dispatch(
            {
                "id": 3,
                "method": "session.run",
                "params": {
                    "session_id": session_id,
                    "input": {"content": [{"type": "text", "text": "Hello"}]},
                },
            }
        )
    )
    try:
        await asyncio.wait_for(probe.entered.wait(), 5)
        await server.dispatch(
            {"id": 4, "method": "session.close", "params": {"session_id": session_id}}
        )
        await asyncio.wait_for(running, 5)
        reply = next(message for message in messages if message.get("id") == 3)
        assert "error" not in reply
        assert reply["result"]["result"].state == "cancelled"
        assert reply["result"]["history"][-1].result == reply["result"]["result"]
        assert probe.active == 0
    finally:
        await server.agents[agent_id].close()
        await running


async def test_collected_result_keeps_its_history_when_session_closes_before_delivery(monkeypatch):
    monkeypatch.setattr(
        assembly,
        "_provider_factory",
        ScriptedFactory([{"chunks": ["Finished"], "text": "Finished"}]),
    )
    terminal_ready = asyncio.Event()
    release = asyncio.Event()
    read = EventJournal._read

    async def pause_terminal(self):
        async for event in read(self):
            if event.type == "terminal":
                terminal_ready.set()
                await release.wait()
            yield event

    monkeypatch.setattr(EventJournal, "_read", pause_terminal)
    agent = await assembly.create_engine(AgentOptions())
    pending = None
    try:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        pending = asyncio.create_task(session.run_with_history(TurnInput([TextPart("Finish")])))
        await asyncio.wait_for(terminal_ready.wait(), 2)
        await session.close()
        with pytest.raises(AgentError, match="closed"):
            _ = session.history
        release.set()
        result, history = await asyncio.wait_for(pending, 2)
        assert result.state == "success"
        assert result.content == [TextPart("Finished")]
        assert len(history) == 1
        assert history[0].input == TurnInput([TextPart("Finish")])
        assert history[0].result == result
    finally:
        release.set()
        await agent.close()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
