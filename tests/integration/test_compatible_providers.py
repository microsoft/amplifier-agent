from datetime import UTC, datetime, timedelta
import json

from amplifier_agent import (
    AgentOptions,
    ContentPart,
    ConversationMessage,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)
import pytest

from tests.support.compatible_services import compatible_service
from tests.support.http_server import socket_server

PROVIDERS = {
    "azure-openai": ("responses", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "gpt-5"),
    "vllm": ("responses", "VLLM_BASE_URL", "VLLM_API_KEY", "gpt-5"),
    "chat-completions": (
        "chat",
        "CHAT_COMPLETIONS_BASE_URL",
        "CHAT_COMPLETIONS_API_KEY",
        "fixture-model",
    ),
    "ollama": ("ollama", "OLLAMA_HOST", "OLLAMA_API_KEY", "fixture-model"),
    "openai-chatgpt": ("chatgpt", None, None, "gpt-5"),
}


def part_text(part: ContentPart) -> str:
    assert isinstance(part, TextPart)
    return part.text


def credentials(monkeypatch, provider, url):
    _protocol, endpoint, key, _model = PROVIDERS[provider]
    if endpoint:
        monkeypatch.setenv(endpoint, url + ("/v1" if provider in {"vllm", "chat-completions"} else ""))
    if key:
        monkeypatch.setenv(key, "fixture-api-key")
    if provider == "openai-chatgpt":
        import amplifier_module_provider_openai_chatgpt.oauth as oauth
        import amplifier_module_provider_openai_chatgpt.provider as module

        monkeypatch.setattr(
            oauth,
            "load_tokens",
            lambda path=None: {
                "access_token": "fixture-token",
                "account_id": "fixture-account",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        monkeypatch.setattr(module, "CHATGPT_CODEX_ENDPOINT", url + "/responses")


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("with_tool", [False, True])
async def test_remaining_http_providers_use_ecosystem_runtime(monkeypatch, provider, with_tool):
    protocol, _endpoint, _key, model = PROVIDERS[provider]
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
    async with socket_server(compatible_service(protocol, requests, tool="record" if with_tool else None)) as url:
        credentials(monkeypatch, provider, url)
        async with (
            await create_agent(
                AgentOptions(provider=provider, model=model, tools=[tool] if with_tool else [], approvals="allow")
            ) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            first = await session.run(TurnInput([TextPart("First input")]))
            assert first.state == "success", first.error
            second = await session.run(TurnInput([TextPart("Continue")]))
            assert second.state == "success", second.error
    assert "First input" in json.dumps(requests[-1])
    assert all(body["model"] == model for body in requests)
    assert first.usage is not None
    assert first.usage.entries[0].provider == provider
    assert effects == ([{"value": "fixture"}] if with_tool else [])
    if protocol in {"responses", "chatgpt"}:
        assert all(body["store"] is False and "previous_response_id" not in body for body in requests)


# Retries each provider module makes on a transient failure. Ollama retries only the connection,
# and its client reports a status once the stream is read; the ChatGPT module does not retry.
RETRIES = {"azure-openai": 5, "vllm": 5, "chat-completions": 3, "ollama": 0, "openai-chatgpt": 0}
RETRYING = [provider for provider in PROVIDERS if RETRIES[provider]]


async def run_turn(monkeypatch, provider, service):
    model = PROVIDERS[provider][3]
    async with socket_server(service) as url:
        credentials(monkeypatch, provider, url)
        async with (
            await create_agent(AgentOptions(provider=provider, model=model)) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")]))
            return [event async for event in turn.events()]


def output_deltas(events):
    return ["".join(part.text for part in event.payload.content) for event in events if event.type == "output_delta"]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("status", [400, 503])
async def test_remaining_http_provider_errors_are_named(monkeypatch, provider, status):
    requests = []
    events = await run_turn(monkeypatch, provider, compatible_service(PROVIDERS[provider][0], requests, failure=status))
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error is not None
    assert result.error.code == "provider_failed"
    assert result.error.remedy
    assert len(requests) == 1 + (RETRIES[provider] if status == 503 else 0)


@pytest.mark.parametrize("provider", RETRYING)
@pytest.mark.parametrize("failure", [{"failure": 503}, {"drop": "before_output"}])
async def test_remaining_http_provider_retries_before_output_once_cleanly(monkeypatch, provider, failure):
    requests = []
    service = compatible_service(PROVIDERS[provider][0], requests, failing=1, **failure)
    events = await run_turn(monkeypatch, provider, service)
    result = events[-1].payload
    assert result.state == "success", result.error
    assert output_deltas(events) == ["Wire ", "reply"]
    assert len(requests) == 2


@pytest.mark.parametrize("provider", RETRYING)
async def test_remaining_http_provider_drop_after_output_is_retryable_without_repeating(monkeypatch, provider):
    requests = []
    service = compatible_service(PROVIDERS[provider][0], requests, drop="after_output", failing=1)
    events = await run_turn(monkeypatch, provider, service)
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error is not None
    assert (result.error.code, result.error.retryable) == ("provider_failed", True)
    assert "after output began" in result.error.remedy
    assert output_deltas(events) == ["Wire "]
    assert len(requests) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_compatible_provider_seed_roles_and_parts(monkeypatch, provider):
    protocol, _, _, model = PROVIDERS[provider]
    requests = []
    history = [
        ConversationMessage(role, [TextPart(role + " one"), TextPart(role + " two")])
        for role in ["user", "developer", "assistant", "system"]
    ]
    async with socket_server(compatible_service(protocol, requests)) as url:
        credentials(monkeypatch, provider, url)
        async with (
            await create_agent(
                AgentOptions(provider=provider, model=model, instructions="Configured instructions")
            ) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            result = await session.run(TurnInput([], history=history))
            assert result.state == "success", result.error
    body = requests[0]
    if protocol in {"responses", "chatgpt"}:
        native = body["input"]
        assert body["instructions"] == "Configured instructions"
    else:
        native = body["messages"][1:]
    assert len(native) == len(history)
    for original, message in zip(history, native, strict=True):
        if protocol == "ollama":
            context = json.loads(message["content"])["conversation_context"]
            assert context == {
                "role": original.role,
                "content": [{"type": "text", "text": part_text(part)} for part in original.content],
            }
        else:
            assert message["role"] == original.role
            assert [part["text"] for part in message["content"]] == [part_text(part) for part in original.content]
