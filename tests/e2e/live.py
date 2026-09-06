"""Stream a live turn through an installed binding and reopen its durable transcript."""

import asyncio
import dataclasses
import json
import os
from decimal import Decimal
from pathlib import Path

from amplifier_agent import (
    AgentOptions,
    ApprovalResponse,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)


def normalize(value):
    if dataclasses.is_dataclass(value):
        return {key: normalize(item) for key, item in vars(value).items() if item is not None}
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


async def main():
    resume = os.environ["E2E_MODE"] == "resume"
    effects = []

    async def approve(request):
        return ApprovalResponse("allow" if request.name == "record_probe" else "deny")

    async def record(arguments, context):
        effects.append(arguments)
        return "Probe recorded successfully."

    options = AgentOptions(
        tools=[
            Tool(
                "record_probe",
                "Record a harmless in-memory probe",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                record,
            )
        ],
        approvals=approve,
        instructions="Use only record_probe when asked for a tool. Keep responses short.",
    )
    async with await create_agent(options) as agent:
        session = (
            await agent.resume_session("live-session")
            if resume
            else await agent.create_session(SessionOptions(session_id="live-session"))
        )
        before = normalize(session.history)
        expected = Path(os.environ["E2E_EXPECTED_HISTORY"])
        if resume:
            assert before == json.loads(expected.read_text())
        prompt = (
            "Which value did record_probe record? Reply with only that value; do not call tools."
            if resume
            else "Call record_probe exactly once with value 'ok', then reply OK."
        )
        turn = await session.start_turn(TurnInput([TextPart(prompt)]))
        events = [event async for event in turn.events()]
        result = events[-1].payload
        assert result.state == "success", result.error.code if result.error else result.state
        assert events[0].type == "turn_started" and events[-1].type == "terminal"
        assert any(event.type == "output_delta" for event in events)
        assert session.history[-1].result == result
        assert len(session.history) == (2 if resume else 1)
        assert effects == ([] if resume else [{"value": "ok"}])
        assert result.usage.entries
        if resume:
            assert events[0].payload.continuation == "resumed"
            assert "ok" in "".join(part.text for part in result.content).lower()
        else:
            expected.write_text(json.dumps(normalize(session.history)))
        await session.close()
    print(
        json.dumps(
            {
                "kind": "result",
                "state": result.state,
                "streamed": True,
                "effects": len(effects),
                "history_count": 2 if resume else 1,
                "pid": os.getpid(),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
