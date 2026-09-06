import asyncio
import copy
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ConversationMessage,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.scripted_provider import ScriptedFactory

SCENARIOS = json.loads(
    (Path(__file__).parents[2] / "conformance/scenarios/sessions.json").read_text()
)
PROMPT = TurnInput([TextPart("Another greeting")])


@pytest.fixture
def provider(monkeypatch):
    from amplifier_agent_engine._engine import assembly

    factory = ScriptedFactory()
    monkeypatch.setattr(assembly, "_provider_factory", factory)
    return factory


def options(storage, **values):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", storage=storage, **values)


def input_record(data):
    return TurnInput(
        [TextPart(part["text"]) for part in data["content"]],
        history=[
            ConversationMessage(item["role"], [TextPart(part["text"]) for part in item["content"]])
            for item in data["history"]
        ]
        if "history" in data
        else None,
    )


def named(error, code):
    assert error.value.code == code
    assert error.value.remedy


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda case: case["id"])
async def test_shared_session_scenarios(provider, tmp_path, scenario):
    async with await create_agent(options(tmp_path)) as agent:
        parent = await agent.create_session(SessionOptions(persistence=scenario["persistence"]))
        inputs = [input_record(item) for item in scenario["turns"]]
        for index, input in enumerate(inputs):
            turn = await parent.start_turn(input)
            events = [event async for event in turn.events()]
            assert events[0].payload.continuation == ("fresh" if index == 0 else "resumed")
            assert events[-1].payload.state == "success"
            assert parent.history[-1].result == events[-1].payload
        assert [turn.input for turn in parent.history] == inputs
        before = parent.history
        child = await parent.fork()
        assert uuid.UUID(child.info.session_id).version == 4
        assert child.info.session_id != parent.info.session_id
        assert child.info.persistence == parent.info.persistence
        assert child.history == before
        seed = TurnInput(
            [], history=[ConversationMessage("assistant", [TextPart("Earlier reply")])]
        )
        if inputs or scenario["persistence"] == "durable":
            with pytest.raises(AgentError) as error:
                await child.start_turn(seed)
            named(error, "invalid_input")
        turn = await child.start_turn(PROMPT)
        events = [event async for event in turn.events()]
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert sum(event.type == "terminal" for event in events) == 1
        assert child.history[-1].result == events[-1].payload
        assert events[0].payload.continuation == ("resumed" if inputs else "fresh")
        assert len(child.history) == len(before) + 1
        assert parent.history == before
        grandchild = await child.fork()
        assert grandchild.history == child.history
        await grandchild.close()
        child_id, history = child.info.session_id, child.history
        await child.close()
        for attribute in ("info", "history"):
            with pytest.raises(AgentError) as error:
                getattr(child, attribute)
            named(error, "closed")
        if scenario["persistence"] == "durable":
            resumed = await agent.resume_session(child_id)
            assert resumed.history == history
            await resumed.close()
        else:
            with pytest.raises(AgentError) as error:
                await agent.resume_session(child_id)
            named(error, "not_found")
        await parent.close()


async def test_default_persistence_ownership_and_delete(provider, tmp_path):
    async with (
        await create_agent(options(tmp_path)) as first,
        await create_agent(options(tmp_path)) as second,
    ):
        session = await first.create_session(SessionOptions(session_id="shared-session"))
        assert session.info.persistence == "durable"
        for operation, code in (
            (
                lambda: second.create_session(SessionOptions(session_id="shared-session")),
                "already_exists",
            ),
            (lambda: second.resume_session("shared-session"), "session_in_use"),
            (lambda: second.delete_session("shared-session"), "session_in_use"),
            (lambda: second.resume_session("missing-session"), "not_found"),
        ):
            with pytest.raises(AgentError) as error:
                await operation()
            named(error, code)
        assert await second.list_sessions() == [session.info]
        result = await session.run(PROMPT)
        history = session.history
        await session.close()
        resumed = await second.resume_session("shared-session")
        assert resumed.history == history
        assert resumed.history[0].result == result
        turn = await resumed.start_turn(PROMPT)
        events = [event async for event in turn.events()]
        assert events[0].payload.continuation == "resumed"
        await resumed.close()
        await first.delete_session("shared-session")
        assert await first.list_sessions() == []
        for operation in (second.resume_session, first.delete_session):
            with pytest.raises(AgentError) as error:
                await operation("shared-session")
            named(error, "not_found")


async def test_resume_revalidates_saved_ceiling_and_releases_a_refused_lease(provider, tmp_path):
    higher = AgentOptions(provider="anthropic", model="claude-opus-5", storage=tmp_path)
    async with await create_agent(higher) as agent:
        parent = await agent.create_session(SessionOptions(session_id="higher-session"))
        refined = await agent.create_session(
            SessionOptions(session_id="refined-session", model="claude-sonnet-5")
        )
        child = await refined.fork()
        with pytest.raises(AgentError) as error:
            await child.start_turn(TurnInput([TextPart("Too expensive")], model="claude-opus-5"))
        named(error, "selector_rejected")
        assert provider.requests == []
        await parent.close()
    async with await create_agent(options(tmp_path)) as agent:
        with pytest.raises(AgentError) as error:
            await agent.resume_session("higher-session")
        named(error, "selector_rejected")
        resumed = await agent.resume_session("refined-session")
        turn = await resumed.start_turn(PROMPT)
        events = [event async for event in turn.events()]
        assert events[0].payload.primary_actual.model == "claude-sonnet-5"
    async with await create_agent(higher) as agent:
        resumed = await agent.resume_session("higher-session")
        assert resumed.history == []


@pytest.mark.parametrize("session_id", ["short", "UPPERCASE", "-badstart", "a" * 65])
async def test_invalid_session_identity(provider, tmp_path, session_id):
    async with await create_agent(options(tmp_path)) as agent:
        with pytest.raises(AgentError) as error:
            await agent.create_session(SessionOptions(session_id=session_id))
        named(error, "session_id_invalid")
        assert await agent.list_sessions() == []


async def test_storage_and_workspace_are_snapshotted_and_isolated(provider, monkeypatch, tmp_path):
    monkeypatch.setenv("AMPLIFIER_AGENT_WORKSPACE", "first")
    first = await create_agent(options(tmp_path))
    monkeypatch.setenv("AMPLIFIER_AGENT_WORKSPACE", "second")
    second = await create_agent(options(tmp_path))
    other = await create_agent(options(tmp_path / "other"))
    try:
        session = await first.create_session(SessionOptions(session_id="same-session"))
        assert await second.list_sessions() == []
        assert await other.list_sessions() == []
        independent = await second.create_session(SessionOptions(session_id="same-session"))
        await session.run(TurnInput([TextPart("First workspace")]))
        await independent.run(TurnInput([TextPart("Second workspace")]))
        assert "First workspace" not in json.dumps(provider.requests[-1])
    finally:
        await asyncio.gather(first.close(), second.close(), other.close())


async def test_seed_refusal_snapshot_and_exact_fork_replay(provider, tmp_path):
    async with await create_agent(
        options(tmp_path, instructions="Configured instructions")
    ) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        invalid_inputs = [
            TurnInput([], history=[]),
            TurnInput([], history=[ConversationMessage("tool", [TextPart("Bad role")])]),
        ]
        for input in invalid_inputs:
            with pytest.raises(AgentError) as error:
                await session.start_turn(input)
            named(error, "invalid_input")
            assert error.value.details["field"].startswith("input.")
        assert provider.requests == []
        assert session.history == []
        seed = TurnInput(
            [],
            history=[
                ConversationMessage("system", [TextPart("Historical system")]),
                ConversationMessage("developer", [TextPart("First "), TextPart("second")]),
                ConversationMessage("user", [TextPart("Historical user")]),
                ConversationMessage("assistant", [TextPart("Historical reply")]),
            ],
        )
        expected = copy.deepcopy(seed)
        turn = await session.start_turn(seed)
        seed.history[0].content[0].text = "Mutated"
        events = [event async for event in turn.events()]
        assert events[0].payload.continuation == "fresh"
        assert session.history[0].input == expected
        first_messages = provider.requests[0]["messages"]
        assert [message["role"] for message in first_messages] == [
            "system",
            "system",
            "developer",
            "user",
            "assistant",
        ]
        assert [(part["type"], part["text"]) for part in first_messages[2]["content"]] == [
            ("text", "First "),
            ("text", "second"),
        ]
        child = await session.fork()
        await child.run(PROMPT)
        serialized = json.dumps(provider.requests[-1])
        assert serialized.count("Historical system") == 1
        assert serialized.count("Configured instructions") == 1
        assert "Mutated" not in serialized
        assert len(session.history) == 1
        assert len(child.history) == 2
        with pytest.raises(AgentError) as error:
            await child.run(expected)
        named(error, "invalid_input")


async def test_empty_ephemeral_fork_can_accept_its_first_seed(provider, tmp_path):
    async with await create_agent(options(tmp_path)) as agent:
        parent = await agent.create_session(SessionOptions(persistence="ephemeral"))
        child = await parent.fork()
        result = await child.run(
            TurnInput([], history=[ConversationMessage("assistant", [TextPart("Earlier reply")])])
        )
        assert result.state == "success"
        assert parent.history == []


async def test_active_session_refuses_turn_and_fork_without_blocking_others(provider, tmp_path):
    provider.script = [{"block": True}]
    async with await create_agent(options(tmp_path)) as agent:
        blocked = await agent.create_session()
        active = await blocked.start_turn(PROMPT)
        await asyncio.wait_for(provider.entered.wait(), 5)
        for operation in (lambda: blocked.start_turn(PROMPT), blocked.fork):
            with pytest.raises(AgentError) as error:
                await asyncio.wait_for(operation(), 1)
            named(error, "busy")
        provider.script = [{"chunks": ["Independent"], "text": "Independent"}]
        other = await agent.create_session()
        assert (await asyncio.wait_for(other.run(PROMPT), 5)).state == "success"
        await active.cancel()
        events = [event async for event in active.events()]
        assert events[-1].payload.state == "cancelled"


@pytest.mark.parametrize("outcome", ["success", "failure", "rejected", "cancelled"])
async def test_terminal_outcomes_and_exact_usage_survive_resume(provider, tmp_path, outcome):
    tool = Tool(
        "counter",
        "Return a value",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        lambda args, context: asyncio.sleep(0, result="7"),
    )
    provider.script = [
        {
            "chunks": ["Partial"],
            "text": "Partial",
            "usage": {
                "input_tokens": 9007199254740993,
                "output_tokens": 3,
                "total_tokens": 9007199254740996,
                "cost_usd": "0.000000000000000007",
            },
            **({"failure": True} if outcome == "failure" else {}),
            **({"block": True} if outcome == "cancelled" else {}),
            **({"tool": {"name": "counter", "arguments": {}}} if outcome == "rejected" else {}),
        }
    ]
    async with await create_agent(options(tmp_path, tools=[tool], approvals="deny")) as agent:
        session = await agent.create_session()
        session_id = session.info.session_id
        turn = await session.start_turn(PROMPT)
        if outcome == "cancelled":
            await asyncio.wait_for(provider.entered.wait(), 5)
            await turn.cancel()
        events = [event async for event in turn.events()]
        result = events[-1].payload
        assert result.state == outcome
        history = session.history
        await session.close()
    async with await create_agent(options(tmp_path, tools=[tool], approvals="deny")) as agent:
        resumed = await agent.resume_session(session_id)
        assert resumed.history == history
        assert resumed.history[0].result == result
        if outcome in {"success", "rejected"}:
            entry = resumed.history[0].result.usage.entries[0]
            assert entry.tokens_in == 9007199254740993
            assert entry.cost == {"USD": Decimal("0.000000000000000007")}


async def test_resume_reapplies_current_configuration_and_restores_tool_results(provider, tmp_path):
    async def tool(arguments, context):
        return "Recorded effect"

    declaration = Tool(
        "counter",
        "Return a value",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        tool,
    )
    provider.script = [
        {"tool": {"name": "counter", "arguments": {}}},
        {"text": "Finished", "chunks": ["Finished"]},
    ]
    async with await create_agent(
        options(tmp_path, instructions="Old instructions", tools=[declaration], approvals="allow")
    ) as agent:
        session = await agent.create_session()
        session_id = session.info.session_id
        assert (await session.run(PROMPT)).state == "success"
    provider.script = [{"text": "Continued", "chunks": ["Continued"]}]
    async with await create_agent(
        options(tmp_path, instructions="New instructions", tools=[declaration], approvals="deny")
    ) as agent:
        resumed = await agent.resume_session(session_id)
        assert (await resumed.run(PROMPT)).state == "success"
    serialized = json.dumps(provider.requests[-1])
    assert "New instructions" in serialized
    assert "Old instructions" not in serialized
    assert "Recorded effect" in serialized
    assert serialized.count("Finished") == 1


async def test_killed_caller_releases_lease_and_preserves_completed_turn(provider, tmp_path):
    code = """
import asyncio, json, sys
from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
from conformance.fixtures.scripted_provider import install
async def main():
    install([{'text': 'Committed reply', 'chunks': ['Committed reply']}])
    agent = await create_agent(AgentOptions(storage=sys.argv[1]))
    session = await agent.create_session(SessionOptions(session_id='crash-session'))
    result = await session.run(TurnInput([TextPart('Committed input')]))
    print(json.dumps({'ready': result.state}), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(tmp_path),
        cwd=Path(__file__).parents[2],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        line = await asyncio.wait_for(child.stdout.readline(), 10)
        assert json.loads(line) == {"ready": "success"}
        async with await create_agent(options(tmp_path)) as agent:
            with pytest.raises(AgentError) as error:
                await agent.resume_session("crash-session")
            named(error, "session_in_use")
            child.kill()
            await asyncio.wait_for(child.wait(), 5)
            resumed = await agent.resume_session("crash-session")
            assert resumed.history[0].input == TurnInput([TextPart("Committed input")])
            assert resumed.history[0].result.content == [TextPart("Committed reply")]
            assert (await resumed.run(PROMPT)).state == "success"
            serialized = json.dumps(provider.requests[-1])
            assert serialized.count("Committed input") == 1
            assert serialized.count("Committed reply") == 1
    finally:
        if child.returncode is None:
            child.kill()
            await asyncio.wait_for(child.wait(), 5)
