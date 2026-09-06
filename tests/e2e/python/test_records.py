"""Preserve evolved records through public operations and caller callbacks."""

import copy
import json
from datetime import datetime
from decimal import Decimal

import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ApprovalResponse,
    SessionOptions,
    TextPart,
    Tool,
    ToolFailed,
    TurnInput,
    create_agent,
)

from conformance.fixtures.carriage import RECORDS, install
from conformance.fixtures.engine import provision as provision_engine
from conformance.record_observations import error_record, event_record, observation


@pytest.fixture
def provision(monkeypatch, tmp_path, request):

    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("AMPLIFIER_AGENT_STORAGE", str(tmp_path / "storage"))
    provision_engine(monkeypatch)
    if request.config.getoption("engine") == "production":
        install(monkeypatch)


def verify_evolution(events):
    for event in events:
        event_record(event)
        if event.type == "progress":
            json.dumps(event.payload.data, allow_nan=False)
        assert event.at == datetime.fromisoformat(RECORDS["at"].replace("Z", "+00:00"))
        assert getattr(event, "org.example.envelope") == RECORDS["envelope_extension"]
        if event.type != "org.example.terminal":
            assert event.payload.future_optional == RECORDS["payload_extension"]
            assert getattr(event.payload, "org.example.payload") == RECORDS["payload_extension"]


async def test_evolved_events_deadline_and_exact_values_cross_public_binding(provision):
    callbacks, approvals = [], []

    async def handler(arguments, context):
        callbacks.append((arguments, context))
        return "Recorded"

    async def approve(request):
        approvals.append(request)
        return ApprovalResponse("allow")

    options = AgentOptions(
        tools=[
            Tool(
                "conformance_records",
                "Record exact values",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                },
                handler,
            )
        ],
        approvals=approve,
    )
    input = TurnInput([TextPart("conformance-script:" + json.dumps(RECORDS["provider"]))])
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(input)
            events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success"
    verify_evolution(events)
    types = [event.type for event in events]
    assert types == RECORDS["event_order"]
    assert set(types) == {
        "turn_started",
        "output_delta",
        "reasoning_delta",
        "reasoning_final",
        "tool_call",
        "tool_result",
        "approval_request",
        "approval_decision",
        "progress",
        "usage",
        "terminal",
        "org.example.terminal",
    }
    assert len(callbacks) == len(approvals) == 1
    call = next(event.payload.call for event in events if event.type == "tool_call")
    assert call.source == "caller" and call.name == "conformance_records"
    assert callbacks[0][0] == call.arguments == RECORDS["provider"][0]["tool"]["arguments"]
    assert callbacks[0][1].call_id == call.call_id == approvals[0].call_id
    assert (
        callbacks[0][1].deadline
        == call.deadline
        == datetime.fromisoformat(RECORDS["deadline"].replace("Z", "+00:00"))
    )
    final = events[-1].payload
    assert final.content == [TextPart("First"), TextPart(""), TextPart("Second")]
    assert [event.payload.text for event in events if event.type == "reasoning_final"] == [
        "First thought"
    ]
    entry = final.usage.entries[0]
    assert (
        entry.tokens_in,
        entry.tokens_out,
        entry.cache_read_tokens,
        entry.cache_write_tokens,
    ) == (9007199254740995, 5, 0, 0)
    assert entry.cost == {"USD": Decimal(RECORDS["expected_cost"])}
    assert [event.payload.snapshot for event in events if event.type == "usage"][-1] == final.usage
    assert [event.payload.data for event in events if event.type == "progress"] == [
        RECORDS["progress"]
    ] * 2
    owned = next(event for event in events if event.type == "org.example.terminal")
    assert owned.payload == RECORDS["provider"][1]["events"][0]["data"]["payload"]
    assert getattr(owned, "org.example.owned") == "Unchanged"
    assert types.index("org.example.terminal") < types.index("output_delta")
    for field in ["future_optional", "org.example.payload"]:
        broken = copy.deepcopy(events)
        delattr(broken[0].payload, field)
        with pytest.raises(AttributeError):
            verify_evolution(broken)
    broken = copy.deepcopy(events)
    getattr(broken[0], "org.example.envelope")["integer"] = 9007199254740996
    with pytest.raises(AssertionError):
        verify_evolution(broken)
    broken = copy.deepcopy(events)
    progress = next(event for event in broken if event.type == "progress")
    progress.payload.data["completed_items"] = float("nan")
    with pytest.raises(ValueError):
        verify_evolution(broken)


async def test_owned_terminal_and_progress_cannot_turn_failure_into_success(provision):
    script = [{"events": RECORDS["provider"][1]["events"], "failure": True}]
    async with await create_agent(AgentOptions()) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(
                TurnInput([TextPart("conformance-script:" + json.dumps(script))])
            )
            events = [event async for event in turn.events()]
    assert any(event.type == "progress" for event in events)
    assert any(event.type == "org.example.terminal" for event in events)
    assert events[-1].type == "terminal"
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == "provider_failed"


async def test_closed_error_matches_shared_cross_binding_record(provision):
    agent = await create_agent(AgentOptions())
    session = await agent.create_session(SessionOptions(persistence="ephemeral"))
    await agent.close()
    for operation in [
        agent.list_sessions,
        lambda: session.start_turn(TurnInput([TextPart("Closed")])),
    ]:
        with pytest.raises(AgentError) as caught:
            await operation()
        assert {
            key: value for key, value in vars(caught.value).items() if value is not None
        } == RECORDS["closed"]


@pytest.mark.parametrize("phase", ["method", "terminal"])
async def test_native_errors_preserve_all_fields_and_owned_additions(provision, phase):
    if phase == "method":
        with pytest.raises(AgentError) as caught:
            await create_agent(AgentOptions(instructions=RECORDS["method_error_marker"]))
        result = caught.value
    else:
        async with await create_agent(AgentOptions()) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                turn = await session.start_turn(
                    TurnInput([TextPart(RECORDS["terminal_error_marker"])])
                )
                events = [event async for event in turn.events()]
        assert events[-1].payload.state == "failure"
        result = events[-1].payload.error
    assert type(result) is AgentError
    error_record(result)
    assert vars(result) == RECORDS["error"]


async def test_public_error_and_event_registries_reject_broken_observations(provision):
    async def handler(arguments, context):
        raise ToolFailed("The fixture rejected this effect")

    options = AgentOptions(
        tools=[
            Tool(
                "observe",
                "Observe",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                },
                handler,
            )
        ],
        approvals="allow",
    )
    errors = []
    script = [{"tool": {"name": "observe", "arguments": {}}}]
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            with pytest.raises(AgentError) as caught:
                await session.start_turn(TurnInput([]))
            errors.append(caught.value)
            turn = await session.start_turn(
                TurnInput([TextPart("conformance-script:" + json.dumps(script))])
            )
            events = [event async for event in turn.events()]
    for event in events:
        event_record(event)
        if event.type == "terminal":
            errors.append(event.payload.error)
        elif event.type == "tool_result":
            errors.append(event.payload.resolution.error)
    assert [error.code for error in errors] == ["invalid_input", "tool_failed", "tool_failed"]
    for error in errors:
        error_record(error)
        for field, value in [
            ("code", "unregistered"),
            ("code", "org.example."),
            ("category", "unregistered"),
            ("remedy", ""),
            ("retryable", "false"),
        ]:
            broken = copy.deepcopy(error)
            setattr(broken, field, value)
            with pytest.raises(AssertionError):
                error_record(broken)
    for name in ["UnqualifiedEvent", "unregistered", "org.example."]:
        broken = copy.deepcopy(events[0])
        broken.type = name
        with pytest.raises(AssertionError):
            event_record(broken)


async def test_shared_run_resume_and_fork_results_and_event_order(provision):
    input = TurnInput([TextPart(**part) for part in RECORDS["continuation_input"]["content"]])
    async with await create_agent(AgentOptions()) as agent:
        original = await agent.create_session()
        session_id = original.info.session_id
        result = await original.run(input)
        assert observation(result) == RECORDS["continuation_result"]
        history = original.history
        await original.close()
        async with await agent.resume_session(session_id) as resumed:
            assert resumed.history == history
            turn = await resumed.start_turn(input)
            events = [event async for event in turn.events()]
            assert [event.type for event in events] == RECORDS["continuation_event_order"]
            assert events[0].payload.continuation == "resumed"
            assert observation(events[-1].payload) == RECORDS["continuation_result"]
            history = resumed.history
            async with await resumed.fork() as child:
                assert child.info.session_id != session_id
                assert child.info.persistence == resumed.info.persistence
                assert child.history == history
                turn = await child.start_turn(input)
                events = [event async for event in turn.events()]
                assert [event.type for event in events] == RECORDS["continuation_event_order"]
                assert events[0].payload.continuation == "resumed"
                assert observation(events[-1].payload) == RECORDS["continuation_result"]
                assert resumed.history == history
                assert len(child.history) == len(history) + 1
