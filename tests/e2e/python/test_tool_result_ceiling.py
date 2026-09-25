import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.engine import provision

SCHEMA = "https://json-schema.org/draft/2020-12/schema"
DEFAULT_CEILING = 131_072


def reporter(size):
    async def handler(arguments, context):
        return "x" * size

    return Tool("reporter", "Return a sized report.", {"$schema": SCHEMA, "type": "object"}, handler)


def marker(kept, total):
    return f"...[tool output reached limit: kept {kept} of {total} bytes]"


async def resolve(monkeypatch, size, **configuration):
    factory = provision(monkeypatch, [
        {"tool": {"name": "reporter", "arguments": {}}}, {"text": "Read"},
    ])
    options = AgentOptions(
        provider="anthropic", model="claude-sonnet-5", approvals="allow",
        tools=[reporter(size)], **configuration,
    )
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Report the work")]))
            events = [event async for event in turn.events()]
    resolution = next(event.payload.resolution for event in events if event.type == "tool_result")
    replayed = [
        message for message in factory.requests[-1]["messages"] if message["role"] == "tool"
    ]
    return resolution, replayed


async def test_a_result_above_the_configured_ceiling_reaches_the_model_shortened(monkeypatch):
    resolution, replayed = await resolve(monkeypatch, 10_240, tool_result_max_bytes=100)
    assert resolution.outcome == "completed"
    assert resolution.truncated is True
    assert resolution.original_bytes == 10_240
    assert resolution.content == "x" * 100 + "\n" + marker(100, 10_240)
    assert len(replayed) == 1
    assert marker(100, 10_240) in replayed[0]["content"]
    assert replayed[0]["content"].count("x") == 100


async def test_the_default_ceiling_bounds_a_result_no_caller_configured(monkeypatch):
    resolution, replayed = await resolve(monkeypatch, 300_000)
    assert resolution.truncated is True
    assert resolution.original_bytes == 300_000
    assert resolution.content == "x" * DEFAULT_CEILING + "\n" + marker(DEFAULT_CEILING, 300_000)
    assert replayed[0]["content"].count("x") == DEFAULT_CEILING


async def test_a_disabled_ceiling_carries_the_whole_result(monkeypatch):
    resolution, replayed = await resolve(monkeypatch, 300_000, tool_result_max_bytes=None)
    assert resolution.truncated is False
    assert resolution.original_bytes is None
    assert resolution.content == "x" * 300_000
    assert replayed[0]["content"].count("x") == 300_000


async def test_a_result_within_the_ceiling_keeps_no_marker(monkeypatch):
    resolution, replayed = await resolve(monkeypatch, 64, tool_result_max_bytes=64)
    assert resolution.truncated is False
    assert resolution.original_bytes is None
    assert resolution.content == "x" * 64
    assert "tool output reached limit" not in replayed[0]["content"]


@pytest.mark.parametrize("ceiling", [0, -1, True, "big", 1.5])
async def test_an_invalid_ceiling_is_refused_before_any_work(monkeypatch, ceiling):
    factory = provision(monkeypatch, [{"text": "Unreachable"}])
    with pytest.raises(AgentError) as caught:
        await create_agent(AgentOptions(
            provider="anthropic", model="claude-sonnet-5", tool_result_max_bytes=ceiling,
        ))
    assert caught.value.code == "invalid_input"
    assert caught.value.remedy
    assert not factory.requests
