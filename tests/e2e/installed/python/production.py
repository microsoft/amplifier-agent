"""Exercise an installed Python binding using only its public surface."""

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
    if dataclasses.is_dataclass(value) or isinstance(value, Exception):
        return {key: normalize(item) for key, item in vars(value).items() if item is not None}
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return str(value)
    return value


def output(value):
    print(json.dumps(normalize(value), allow_nan=False), flush=True)


async def main():
    mode = os.environ["E2E_MODE"]
    provider = os.environ["AMPLIFIER_AGENT_PROVIDER"]
    model = os.environ["AMPLIFIER_AGENT_MODEL"]
    approved = False

    async def approve(request):
        nonlocal approved
        approved = True
        return ApprovalResponse("allow")

    async def counter(arguments, context):
        assert approved
        with Path(os.environ["E2E_EFFECT_LEDGER"]).open("a") as ledger:
            ledger.write(json.dumps({"pid": os.getpid(), "call_id": context.call_id}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        return "effect-recorded"

    ephemeral = mode.startswith("ephemeral")
    tools = (
        []
        if ephemeral
        else [
            Tool(
                "counter",
                "Record an effect",
                {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
                counter,
            )
        ]
    )
    async with await create_agent(
        AgentOptions(provider=provider, model=model, tools=tools, approvals=approve)
    ) as agent:
        session = (
            await agent.resume_session(os.environ["E2E_SESSION_ID"])
            if mode == "resume"
            else await agent.create_session(
                SessionOptions(persistence="ephemeral")
                if ephemeral
                else SessionOptions(session_id=os.environ["E2E_SESSION_ID"])
            )
        )
        before = normalize(session.history)
        if mode == "resume":
            assert before == json.loads(Path(os.environ["E2E_EXPECTED_HISTORY"]).read_text())
        turn = await session.start_turn(
            TurnInput([TextPart("Resume this conversation" if mode == "resume" else "Hello")])
        )
        events = []
        async for event in turn.events():
            events.append(event)
            if event.type == "output_delta":
                output({"kind": "output", "content": event.payload.content})
                if mode == "ephemeral_cancel":
                    await turn.cancel()
        result = events[-1].payload
        assert events[0].type == "turn_started"
        assert events[-1].type == "terminal"
        expected_state = (
            "cancelled"
            if mode == "ephemeral_cancel"
            else "failure"
            if mode.endswith("failure")
            else "success"
        )
        assert result.state == expected_state, normalize(result)
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert [
            part
            for event in events
            if event.type == "output_delta"
            for part in event.payload.content
        ] == result.content
        if expected_state == "success":
            assert "".join(part.text for part in result.content) == "Wire reply"
        else:
            assert result.error.remedy
            assert result.error.code == (
                "turn_cancelled" if expected_state == "cancelled" else "provider_failed"
            )
            if mode in {"ephemeral_cancel", "ephemeral_partial_failure"}:
                assert result.content == [TextPart("Wire ")]
        assert session.history[-1].result == result
        entry = result.usage.entries[0]
        assert (entry.provider, entry.model) == (provider, model)
        if expected_state == "success":
            assert (entry.tokens_in, entry.tokens_out) == ((40, 4) if mode == "create" else (20, 2))
        if mode == "create":
            assert sum(event.type == "tool_call" for event in events) == 1
            assert sum(event.type == "tool_result" for event in events) == 1
            assert sum(event.type == "approval_request" for event in events) == 1
            assert sum(event.type == "approval_decision" for event in events) == 1
        if mode == "resume":
            assert events[0].payload.continuation == "resumed"
            assert not any(event.type == "tool_call" for event in events)
        output(
            {
                "kind": "result",
                "pid": os.getpid(),
                "before": before,
                "history": session.history,
                "result": result,
            }
        )
        if mode == "create":
            await asyncio.Event().wait()
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
