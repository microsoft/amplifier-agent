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


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_remaining_http_provider_errors_are_named(monkeypatch, provider):
    protocol, _endpoint, _key, model = PROVIDERS[provider]
    requests = []
    async with socket_server(compatible_service(protocol, requests, failure=503)) as url:
        credentials(monkeypatch, provider, url)
        async with (
            await create_agent(AgentOptions(provider=provider, model=model)) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            result = await session.run(TurnInput([TextPart("Hello")]))
    assert result.state == "failure"
    assert result.error is not None
    assert result.error.code == "provider_failed"
    assert result.error.remedy
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
