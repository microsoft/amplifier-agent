from decimal import Decimal

from amplifier_agent import AgentOptions, SessionOptions, TextPart, Tool, TurnInput, create_agent

from conformance.fixtures.scripted_provider import ScriptedFactory


async def test_exact_cumulative_usage_tool_arguments_and_owned_events(monkeypatch):
    from amplifier_agent_engine._engine import assembly

    large = 9007199254740993
    extension = {
        "type": "org.example.exact",
        "data": {
            "payload": {"integer": large, "parts": ["first", "second"]},
            "org.example.counter": large + 2,
        },
    }
    probe = ScriptedFactory(
        [
            {
                "tool": {"name": "counter", "arguments": {"value": large}},
                "usage": {
                    "input_tokens": large,
                    "output_tokens": 2,
                    "total_tokens": large + 2,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_usd": "0.123456789012345678901234567890123",
                },
            },
            {
                "chunks": ["Exact"],
                "text": "Exact",
                "events": [extension],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 5,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_usd": "0.000000000000000000000000000000002",
                },
            },
        ]
    )
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    arguments_seen = []

    async def handler(arguments, context):
        arguments_seen.append(arguments)
        return str(arguments["value"])

    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        approvals="allow",
        tools=[
            Tool(
                "counter",
                "Record an exact integer",
                {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
                handler,
            ),
        ],
    )
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Preserve exact values")]))
            events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success"
    assert arguments_seen == [{"value": large}]
    usage = events[-1].payload.usage.entries[0]
    assert usage.tokens_in == large + 2
    assert usage.tokens_out == 5
    assert usage.cache_read_tokens == usage.cache_write_tokens == 0
    assert usage.cost["USD"] == Decimal("0.123456789012345678901234567890125")
    assert [event.payload.snapshot for event in events if event.type == "usage"][-1] == events[
        -1
    ].payload.usage
    owned = [event for event in events if event.type == "org.example.exact"]
    assert len(owned) == 1
    assert owned[0].payload == extension["data"]["payload"]
    assert getattr(owned[0], "org.example.counter") == large + 2
