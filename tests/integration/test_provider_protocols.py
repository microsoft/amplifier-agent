import asyncio
import json
from typing import Any

from amplifier_agent import (
    AgentError,
    AgentOptions,
    ContentPart,
    ConversationMessage,
    ImagePart,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)
import pytest

from tests.support.http_server import socket_server
from tests.support.provider_services import KEY_ENV, MODELS, URL_ENV, provider_service, qualified_openai_route


def part_text(part: ContentPart) -> str:
    assert isinstance(part, TextPart)
    return part.text


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_protocol_delivers_live_output_and_exact_usage(monkeypatch, provider):
    requests = []
    release, received = asyncio.Event(), asyncio.Event()
    async with socket_server(provider_service(provider, requests, release=release)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with (
            await create_agent(
                AgentOptions(provider=provider, model=MODELS[provider], instructions="Server instructions")
            ) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
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
    request = requests[0]
    if provider != "gemini":
        assert request["stream"] is True
    native_instructions = {"anthropic": "system", "openai": "instructions", "gemini": "systemInstruction"}
    assert "Server instructions" in json.dumps(request[native_instructions[provider]])
    deltas = ["".join(part.text for part in event.payload.content) for event in events if event.type == "output_delta"]
    assert deltas == ["Wire ", "reply"]
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
async def test_provider_seed_preserves_interleaved_roles_and_text_parts(monkeypatch, tmp_path, provider):
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
        async with (
            await create_agent(
                AgentOptions(
                    provider=provider,
                    model=MODELS[provider],
                    instructions="Configured instructions",
                    working_directory=tmp_path,
                    smart_tools=False,
                )
            ) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            result = await session.run(TurnInput([], history=history))
            assert result.state == "success", result.error
    body = requests[0]
    if provider == "openai":
        native = body["input"]
        assert body["instructions"] == "Configured instructions"
        assert [message["role"] for message in native] == [message.role for message in history]
        assert [[part["text"] for part in message["content"]] for message in native] == [
            [part_text(part) for part in message.content] for message in history
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
                    "content": [{"type": "text", "text": part_text(part)} for part in original.content],
                }
            else:
                assert [part["text"] for part in parts] == [part_text(part) for part in original.content]


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
    async with socket_server(provider_service(provider, requests, tool="record", late_signature=True)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with (
            await create_agent(
                AgentOptions(
                    provider=provider,
                    model=MODELS[provider],
                    tools=[tool],
                    approvals="allow",
                    sessions_directory=tmp_path,
                )
            ) as agent,
            await agent.create_session(SessionOptions()) as session,
        ):
            first = await session.run(TurnInput([TextPart("Record first input")]))
            assert first.state == "success", first.error
            second = await session.run(TurnInput([TextPart("Continue conversation")]))
            assert second.state == "success", second.error
    assert effects == [{"value": "fixture"}]
    assert len(requests) == 3
    assert "Record first input" in json.dumps(requests[-1])
    assert "Effect recorded" in json.dumps(requests[-1])
    assert "Continue conversation" in json.dumps(requests[-1])
    assert first.usage is not None
    assert first.usage.entries[0].tokens_in == 40
    if provider == "openai":
        assert all(body["store"] is False and "previous_response_id" not in body for body in requests)


# Retries each provider module makes on a transient failure. Gemini streams without retrying.
RETRIES = {"anthropic": 5, "openai": 5, "gemini": 0}
RETRYING = [provider for provider in MODELS if RETRIES[provider]]


async def run_turn(monkeypatch, provider, service):
    async with socket_server(service) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with (
            await create_agent(AgentOptions(provider=provider, model=MODELS[provider])) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")]))
            return [event async for event in turn.events()]


def delta_texts(events, kind):
    if kind == "output_delta":
        return ["".join(part.text for part in event.payload.content) for event in events if event.type == kind]
    return [event.payload.text for event in events if event.type == kind]


@pytest.mark.parametrize(("provider", "status"), [(provider, status) for provider in MODELS for status in (400, 401)])
async def test_provider_protocol_rejection_is_named_without_retry(monkeypatch, provider, status):
    requests = []
    events = await run_turn(monkeypatch, provider, provider_service(provider, requests, failure=status))
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error is not None
    assert result.error.code == "provider_failed"
    assert result.error.remedy
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("provider", "status"),
    [(provider, status) for provider in MODELS for status in (429, 503)] + [("anthropic", 529)],
)
async def test_provider_protocol_transient_failure_is_retried_then_named(monkeypatch, provider, status):
    requests = []
    events = await run_turn(monkeypatch, provider, provider_service(provider, requests, failure=status))
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error is not None
    assert result.error.code == "provider_failed"
    assert result.error.remedy
    assert len(requests) == 1 + RETRIES[provider]


@pytest.mark.parametrize("provider", RETRYING)
@pytest.mark.parametrize("failure", [{"failure": 503}, {"failure": 429}, {"drop": "before_output"}])
async def test_provider_transient_failure_before_output_is_retried_once_cleanly(monkeypatch, provider, failure):
    requests = []
    events = await run_turn(monkeypatch, provider, provider_service(provider, requests, failing=1, **failure))
    result = events[-1].payload
    assert result.state == "success", result.error
    assert delta_texts(events, "output_delta") == ["Wire ", "reply"]
    assert "".join(part.text for part in result.content) == "Wire reply"
    assert len(requests) == 2


@pytest.mark.parametrize("provider", RETRYING)
@pytest.mark.parametrize("drop", ["after_reasoning", "after_output"])
async def test_provider_drop_after_output_began_is_retryable_without_repeating(monkeypatch, provider, drop):
    requests = []
    service = provider_service(provider, requests, reasoning=True, drop=drop, failing=1)
    events = await run_turn(monkeypatch, provider, service)
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error is not None
    assert (result.error.code, result.error.category, result.error.retryable) == ("provider_failed", "provider", True)
    assert "after output began" in result.error.remedy
    assert delta_texts(events, "reasoning_delta") == ["Considering."]
    assert delta_texts(events, "output_delta") == ([] if drop == "after_reasoning" else ["Wire "])
    assert "".join(part.text for part in result.content) == ("" if drop == "after_reasoning" else "Wire ")
    assert len(requests) == 1


@pytest.mark.parametrize("provider", MODELS)
async def test_provider_stream_eof_preserves_observed_output(monkeypatch, provider):
    requests = []
    async with socket_server(provider_service(provider, requests, partial_failure=True)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with (
            await create_agent(AgentOptions(provider=provider, model=MODELS[provider])) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")]))
            events = [event async for event in turn.events()]
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error is not None
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
        async with (
            await create_agent(AgentOptions(provider="gemini", model=MODELS["gemini"])) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            result = await session.run(TurnInput([TextPart("Hello")]))
    assert result.state == "failure"
    assert result.error is not None
    assert result.error.code == "selector_rejected"
    assert result.usage is not None
    assert len(result.usage.entries) == 1
    entry = result.usage.entries[0]
    assert (entry.provider, entry.model, entry.tokens_in, entry.tokens_out) == (
        "gemini",
        actual,
        20,
        2,
    )


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def _tool_result_wire(provider, body):
    if provider == "anthropic":
        return [
            block["content"]
            for message in body["messages"]
            if isinstance(message["content"], list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
    return [item["output"] for item in body["input"] if item.get("type") == "function_call_output"]


@pytest.mark.parametrize(
    ("provider", "model", "qualified"),
    [
        *((provider, model, True) for provider, model in MODELS.items()),
        ("openai", "gpt-6.2-astra", True),
        ("openai", "gpt-6.2-sol", True),
        ("openai", "gpt-5", False),
    ],
)
async def test_tool_result_images_reach_the_native_tool_result_or_fail_before_sending(
    monkeypatch, provider, model, qualified
):
    requests = []

    async def execute(arguments, context):
        return [TextPart("Caption"), ImagePart(media_type="image/png", data=PNG)]

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
    service = provider_service(provider, requests, tool="record", late_signature=True, reported_model=model)
    async with socket_server(service) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        if provider == "openai" and qualified:
            blocked = qualified_openai_route(monkeypatch, url)
        async with (
            await create_agent(AgentOptions(provider=provider, model=model, tools=[tool], approvals="allow")) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            result = await session.run(TurnInput([TextPart("Record and look")]))
        if provider == "openai" and qualified:
            assert blocked == []
    if provider == "gemini" or not qualified:
        assert result.state == "failure"
        assert result.error is not None
        assert result.error.code == "image_unsupported"
        assert len(requests) == 1
        return
    assert result.state == "success", repr(vars(result.error)) if result.error else None
    assert len(requests) == 2
    expected: list[dict[str, Any]] = {
        "anthropic": [
            {"type": "text", "text": "Caption"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
        ],
        "openai": [
            {"type": "input_text", "text": "Caption"},
            {"type": "input_image", "image_url": f"data:image/png;base64,{PNG}"},
        ],
    }[provider]
    if provider == "openai" and model in {"gpt-6.2-astra", "gpt-6.2-sol"}:
        expected[0]["prompt_cache_breakpoint"] = {"mode": "explicit"}
    assert _tool_result_wire(provider, requests[1]) == [expected]
    if provider == "openai":
        result_item = next(item for item in requests[1]["input"] if item.get("type") == "function_call_output")
        call = next(item for item in requests[1]["input"] if item.get("type") == "function_call")
        assert result_item["call_id"] == call["call_id"]


@pytest.mark.parametrize("history", [False, True])
async def test_openai_images_on_unqualified_endpoints_fail_before_dispatch(monkeypatch, history):
    requests = []
    async with socket_server(provider_service("openai", requests)) as url:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
        monkeypatch.setenv("OPENAI_BASE_URL", url)
        async with (
            await create_agent(AgentOptions(provider="openai", model="gpt-6.2-sol")) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            image = ImagePart(media_type="image/png", data=PNG)
            value = (
                TurnInput([TextPart("Look again")], history=[ConversationMessage("user", [image])])
                if history
                else TurnInput([image])
            )
            with pytest.raises(AgentError) as caught:
                await session.start_turn(value)
            assert caught.value.code == "image_unsupported"
            assert caught.value.remedy
            assert requests == []
            result = await session.run(
                TurnInput([TextPart("Continue")], history=[ConversationMessage("user", [TextPart("Seed retained")])])
            )
    assert result.state == "success"
    assert len(requests) == 1
    assert "Seed retained" in json.dumps(requests[0])


@pytest.mark.parametrize("history", [False, True])
async def test_openai_user_images_reach_the_qualified_native_request(monkeypatch, history):
    requests = []
    service = provider_service("openai", requests, reported_model="gpt-6.2-sol")
    async with socket_server(service) as url:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
        blocked = qualified_openai_route(monkeypatch, url)
        async with (
            await create_agent(AgentOptions(provider="openai", model="gpt-6.2-sol")) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            image = ImagePart(media_type="image/png", data=PNG)
            value = (
                TurnInput([TextPart("Look again")], history=[ConversationMessage("user", [image])])
                if history
                else TurnInput([TextPart("Look"), image])
            )
            result = await session.run(value)
        assert blocked == []
    assert result.state == "success", result.error
    assert len(requests) == 1
    images = [
        block
        for message in requests[0]["input"]
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "input_image"
    ]
    assert images == [{"type": "input_image", "image_url": f"data:image/png;base64,{PNG}"}]
    assert "[Image unavailable" not in json.dumps(requests)
