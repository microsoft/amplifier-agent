"""Observe installed Python handles without importing the producer checkout."""

import asyncio
import json
import os

from amplifier_agent import (
    AgentError,
    AgentOptions,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)


async def main():
    probe = json.loads(os.environ["E2E_CONTRACT_PROBE"])
    effects = []

    async def effect(arguments, context):
        effects.append(context.call_id)
        return "Confirmed effect"

    options = AgentOptions(
        tools=[
            Tool(
                "observe",
                "Observe",
                {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
                effect,
            )
        ]
        if probe.get("tool")
        else [],
        approvals="allow",
    )
    try:
        agent = await create_agent(options)
    except AgentError as error:
        assert probe["mode"] == "invalid_host"
        print(json.dumps({"kind": "error", "error": vars(error)}), flush=True)
        return
    assert probe["mode"] != "invalid_host"
    events, histories = [], []
    try:
        session = await agent.create_session(SessionOptions(session_id="installed-contract"))
        for index in range(probe["rounds"]):
            if index and probe["mode"] == "restart":
                await session.close()
                await agent.close()
                agent = await create_agent(options)
                session = await agent.resume_session("installed-contract")
            turn = await session.start_turn(TurnInput([TextPart(f"Visible question {index}")]))
            observed = [event async for event in turn.events()]
            assert [event.sequence for event in observed] == list(range(1, len(observed) + 1))
            assert observed[0].type == "turn_started" and observed[-1].type == "terminal"
            result = observed[-1].payload
            assert result.state == "success", vars(result.error) if result.error else result
            assert "".join(part.text for part in result.content) == "Wire reply"
            assert session.history[-1].result == result
            events.append([event.type for event in observed])
            histories.append(len(session.history))
        await session.close()
    finally:
        await agent.close()
    print(
        json.dumps({"kind": "done", "events": events, "histories": histories, "effects": effects}),
        flush=True,
    )


asyncio.run(main())
