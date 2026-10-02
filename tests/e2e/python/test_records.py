"""Preserve evolved records through public operations and caller callbacks."""

from datetime import datetime
from decimal import Decimal
import json

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
import pytest

from tests.support.carriage import RECORDS, install
from tests.support.engine import provision as provision_engine
from tests.support.records import error_record, event_record


@pytest.fixture
def provision(monkeypatch):
    provision_engine(monkeypatch)
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
                "scripted_records",
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
    input = TurnInput([TextPart("scripted:" + json.dumps(RECORDS["provider"]))])
    async with (
        await create_agent(options) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(input)
        events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success"
    verify_evolution(events)
    types = [event.type for event in events]
    assert types == RECORDS["event_order"]
    assert len(callbacks) == len(approvals) == 1
    call = next(event.payload.call for event in events if event.type == "tool_call")
    assert call.source == "caller"
    assert call.name == "scripted_records"
    assert callbacks[0][0] == call.arguments == RECORDS["provider"][0]["tool"]["arguments"]
    assert callbacks[0][1].call_id == call.call_id == approvals[0].call_id
    assert (
        callbacks[0][1].deadline == call.deadline == datetime.fromisoformat(RECORDS["deadline"].replace("Z", "+00:00"))
    )
    final = events[-1].payload
    assert final.content == [TextPart("First"), TextPart(""), TextPart("Second")]
    assert [event.payload.text for event in events if event.type == "reasoning_final"] == ["First thought"]
    entry = final.usage.entries[0]
    assert (
        entry.tokens_in,
        entry.tokens_out,
        entry.cache_read_tokens,
        entry.cache_write_tokens,
    ) == (9007199254740995, 5, 0, 0)
    assert entry.cost == {"USD": Decimal(RECORDS["expected_cost"])}
    assert [event.payload.snapshot for event in events if event.type == "usage"][-1] == final.usage
    assert [event.payload.data for event in events if event.type == "progress"] == [RECORDS["progress"]] * 2
    owned = next(event for event in events if event.type == "org.example.terminal")
    assert owned.payload == RECORDS["provider"][1]["events"][0]["data"]["payload"]
    assert getattr(owned, "org.example.owned") == "Unchanged"
    assert types.index("org.example.terminal") < types.index("output_delta")


async def test_owned_terminal_and_progress_cannot_turn_failure_into_success(provision):
    script = [{"events": RECORDS["provider"][1]["events"], "failure": True}]
    async with (
        await create_agent(AgentOptions()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("scripted:" + json.dumps(script))]))
        events = [event async for event in turn.events()]
    assert any(event.type == "progress" for event in events)
    assert any(event.type == "org.example.terminal" for event in events)
    assert events[-1].type == "terminal"
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == "provider_failed"


@pytest.mark.parametrize("phase", ["method", "terminal"])
async def test_native_errors_preserve_all_fields_and_owned_additions(provision, phase):
    if phase == "method":
        with pytest.raises(AgentError) as caught:
            await create_agent(AgentOptions(instructions=RECORDS["method_error_marker"]))
        result = caught.value
    else:
        async with (
            await create_agent(AgentOptions()) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart(RECORDS["terminal_error_marker"])]))
            events = [event async for event in turn.events()]
        assert events[-1].payload.state == "failure"
        result = events[-1].payload.error
    assert type(result) is AgentError
    error_record(result)
    assert vars(result) == RECORDS["error"]
