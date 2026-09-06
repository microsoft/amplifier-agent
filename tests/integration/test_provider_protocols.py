import asyncio
import json

import pytest
from amplifier_agent import (
    AgentOptions,
    ConversationMessage,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import KEY_ENV, MODELS, URL_ENV, provider_service


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_protocol_delivers_live_output_and_exact_usage(monkeypatch, provider):
    requests = []
    release, received = asyncio.Event(), asyncio.Event()
    async with socket_server(provider_service(provider, requests, release=release)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(
            AgentOptions(provider=provider, model=MODELS[provider])
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                turn = await session.start_turn(TurnInput([TextPart("Hello")]))
                events = []

                async def collect():
                    async for event in turn.events():
                        events.append(event)
                        if event.type == "output_delta":
                            received.set()

                collecting = asyncio.create_task(collect())
                try:
                    await asyncio.wait_for(received.wait(), 10)
                finally:
                    release.set()
                await asyncio.wait_for(collecting, 10)
    assert len(requests) == 1
    result = events[-1].payload
    assert result.state == "success", result.error
    assert "".join(part.text for part in result.content) == "Wire reply"
    entry = result.usage.entries[0]
    assert (entry.provider, entry.model) == (provider, MODELS[provider])
    assert (entry.tokens_in, entry.tokens_out, entry.cache_read_tokens) == (20, 2, 3)
    assert entry.cache_write_tokens == (None if provider == "gemini" else 5)
    if provider == "openai":
        assert requests[0]["store"] is False
        assert "previous_response_id" not in requests[0]


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_seed_preserves_interleaved_roles_and_text_parts(monkeypatch, provider):
    requests = []
    history = [
        ConversationMessage("system", [TextPart("system one"), TextPart("system two")]),
        ConversationMessage("user", [TextPart("user one"), TextPart("user two")]),
        ConversationMessage("developer", [TextPart("developer one"), TextPart("developer two")]),
        ConversationMessage("assistant", [TextPart("assistant one"), TextPart("assistant two")]),
        ConversationMessage("system", [TextPart("last system"), TextPart("")]),
    ]
    async with socket_server(provider_service(provider, requests)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(
            AgentOptions(
                provider=provider, model=MODELS[provider], instructions="Configured instructions"
            )
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                result = await session.run(TurnInput([], history=history))
                assert result.state == "success", result.error
    body = requests[0]
    if provider == "openai":
        native = body["input"]
        assert body["instructions"] == "Configured instructions"
        assert [message["role"] for message in native] == [message.role for message in history]
        assert [[part["text"] for part in message["content"]] for message in native] == [
            [part.text for part in message.content] for message in history
        ]
    else:
        native = body["messages" if provider == "anthropic" else "contents"]
        assert len(native) == len(history)
        for original, message in zip(history, native, strict=True):
            parts = message["content" if provider == "anthropic" else "parts"]
            if original.role in {"system", "developer"}:
                text = parts if isinstance(parts, str) else parts[0]["text"]
                context = json.loads(text)["conversation_context"]
                assert context == {
                    "role": original.role,
                    "content": [{"type": "text", "text": part.text} for part in original.content],
                }
            else:
                assert [part["text"] for part in parts] == [part.text for part in original.content]


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_protocol_preserves_tools_and_full_replay(monkeypatch, provider, tmp_path):
    requests, effects = [], []

    async def execute(arguments, context):
        effects.append(arguments)
        return "Effect recorded"

    tool = Tool(
        "record",
        "Record a value",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        execute,
    )
    async with socket_server(
        provider_service(provider, requests, tool="record", late_signature=True)
    ) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(
            AgentOptions(
                provider=provider,
                model=MODELS[provider],
                tools=[tool],
                approvals="allow",
                storage=tmp_path,
            )
        ) as agent:
            async with await agent.create_session(SessionOptions()) as session:
                first = await session.run(TurnInput([TextPart("Record first input")]))
                assert first.state == "success", first.error
                second = await session.run(TurnInput([TextPart("Continue conversation")]))
                assert second.state == "success", second.error
    assert effects == [{"value": "fixture"}]
    assert len(requests) == 3
    assert "Record first input" in json.dumps(requests[-1])
    assert "Effect recorded" in json.dumps(requests[-1])
    assert "Continue conversation" in json.dumps(requests[-1])
    assert first.usage.entries[0].tokens_in == 40
    if provider == "openai":
        assert all(
            body["store"] is False and "previous_response_id" not in body for body in requests
        )


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("status", [400, 401, 429, 503])
async def test_provider_protocol_failure_is_named_without_retry(monkeypatch, provider, status):
    requests = []
    async with socket_server(provider_service(provider, requests, failure=status)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(
            AgentOptions(provider=provider, model=MODELS[provider])
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                result = await session.run(TurnInput([TextPart("Hello")]))
    assert result.state == "failure"
    assert result.error.code == "provider_failed"
    assert result.error.remedy
    assert len(requests) == 1


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_reasoning_replay_is_local_json(monkeypatch, provider):
    requests = []
    async with socket_server(provider_service(provider, requests, reasoning=True)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(
            AgentOptions(provider=provider, model=MODELS[provider])
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                first = await session.run(TurnInput([TextPart("Think about this")]))
                assert first.state == "success", first.error
                second = await session.run(TurnInput([TextPart("Continue")]))
                assert second.state == "success", second.error
    assert len(requests) == 2
    replay = json.dumps(requests[-1])
    assert "Think about this" in replay
    if provider == "openai":
        assert "opaque-fixture-reasoning" in replay
        assert "previous_response_id" not in requests[-1]
    elif provider == "gemini":
        assert "c2lnbmF0dXJl" in replay
    else:
        assert "fixture-signature" in replay


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_stream_eof_preserves_observed_output(monkeypatch, provider):
    requests = []
    async with socket_server(provider_service(provider, requests, partial_failure=True)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(
            AgentOptions(provider=provider, model=MODELS[provider])
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                turn = await session.start_turn(TurnInput([TextPart("Hello")]))
                events = [event async for event in turn.events()]
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error.code == "provider_failed"
    assert "".join(part.text for part in result.content) == "Wire "
    assert any(event.type == "output_delta" for event in events)
    assert len(requests) == 1


async def test_gemini_stream_accounts_actual_model_before_rejecting_selection(monkeypatch):
    requests = []
    actual = "gemini-unrequested-model"
    async with socket_server(provider_service("gemini", requests, reported_model=actual)) as url:
        monkeypatch.setenv(KEY_ENV["gemini"], "fixture-api-key")
        monkeypatch.setenv(URL_ENV["gemini"], url)
        async with await create_agent(
            AgentOptions(provider="gemini", model=MODELS["gemini"])
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                result = await session.run(TurnInput([TextPart("Hello")]))
    assert result.state == "failure"
    assert result.error.code == "selector_rejected"
    assert len(result.usage.entries) == 1
    entry = result.usage.entries[0]
    assert (entry.provider, entry.model, entry.tokens_in, entry.tokens_out) == (
        "gemini",
        actual,
        20,
        2,
    )
