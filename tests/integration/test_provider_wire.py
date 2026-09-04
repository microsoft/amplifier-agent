import asyncio
import json

import pytest
from amplifier_agent import (
    AgentOptions,
    ConversationMessage,
    SessionOptions,
    TextPart,
    TurnInput,
    create_agent,
)

from conformance.fixtures.anthropic_service import anthropic_service
from conformance.fixtures.http_server import socket_server


async def test_production_anthropic_adapter_streams_selected_model_and_usage(monkeypatch):
    requests = []
    release, received = asyncio.Event(), asyncio.Event()
    async with socket_server(anthropic_service(requests, release=release)) as url:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-api-key")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        async with await create_agent(
            AgentOptions(
                provider="anthropic", model="claude-sonnet-5", instructions="Server instructions"
            )
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                turn = await session.start_turn(
                    TurnInput(
                        [],
                        history=[
                            ConversationMessage("user", [TextPart("First "), TextPart("question")]),
                            ConversationMessage("assistant", [TextPart("Earlier answer")]),
                            ConversationMessage("user", [TextPart("Continue")]),
                        ],
                    )
                )
                events = []

                async def collect():
                    async for event in turn.events():
                        events.append(event)
                        if event.type == "output_delta":
                            received.set()

                collecting = asyncio.create_task(collect())
                try:
                    await asyncio.wait_for(received.wait(), timeout=5)
                finally:
                    release.set()
                await asyncio.wait_for(collecting, timeout=5)
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "claude-sonnet-5"
    assert request["stream"] is True
    assert [message["role"] for message in request["messages"]] == ["user", "assistant", "user"]
    assert [part["text"] for part in request["messages"][0]["content"]] == ["First ", "question"]
    assert "Server instructions" in json.dumps(request["system"])
    assert [
        "".join(part.text for part in event.payload.content)
        for event in events
        if event.type == "output_delta"
    ] == ["Wire ", "reply"]
    result = events[-1].payload
    assert result.state == "success"
    assert [part.text for part in result.content] == ["Wire ", "reply"]
    usage = result.usage.entries[0]
    assert (usage.provider, usage.model) == ("anthropic", "claude-sonnet-5")
    assert (usage.tokens_in, usage.tokens_out) == (20, 2)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (3, 5)


@pytest.mark.parametrize("status", [400, 429, 529])
async def test_production_anthropic_failure_does_not_retry_or_fallback(monkeypatch, status):
    requests = []
    async with socket_server(anthropic_service(requests, failure=status)) as url:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-api-key")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        async with await create_agent(
            AgentOptions(provider="anthropic", model="claude-sonnet-5")
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                result = await session.run(TurnInput([TextPart("Hello")]))
    assert result.state == "failure"
    assert result.error.code == "provider_failed"
    assert result.error.remedy
    assert [request["model"] for request in requests] == ["claude-sonnet-5"]
