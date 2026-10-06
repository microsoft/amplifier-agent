"""The reasoning effort reaches each native provider protocol in its own request field."""

import os
from types import SimpleNamespace

from amplifier_agent import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
import pytest

from tests.support.compatible_services import compatible_service
from tests.support.http_server import socket_server
from tests.support.provider_services import KEY_ENV, URL_ENV, provider_service

# The highest value each fixture model takes, so a turn may name any value it takes.
CEILING = {"openai": "max", "anthropic": "max", "gemini": "high"}


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return path


def native_effort(provider, body):
    if provider == "openai":
        return (body.get("reasoning") or {}).get("effort")
    if provider == "anthropic":
        return (body.get("output_config") or {}).get("effort")
    thinking = (body.get("generationConfig") or {}).get("thinkingConfig") or {}
    level = thinking.get("thinking_level", thinking.get("thinkingLevel"))
    return level.lower() if isinstance(level, str) else level


async def native_turn(monkeypatch, provider, model, effort, requests, **service):
    async with socket_server(provider_service(provider, requests, **service)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with (
            await create_agent(
                AgentOptions(provider=provider, model=model, reasoning_effort=CEILING[provider])
            ) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")], reasoning_effort=effort))
            return [event async for event in turn.events()]


@pytest.mark.parametrize(
    ("provider", "model", "effort"),
    [
        ("openai", "gpt-5", "low"),
        ("openai", "gpt-5", "xhigh"),
        ("anthropic", "claude-sonnet-5", "low"),
        ("anthropic", "claude-sonnet-5", "max"),
        ("gemini", "gemini-3.5-flash", "minimal"),
        ("gemini", "gemini-3.5-flash", "high"),
    ],
)
async def test_named_value_reaches_the_native_request_field(monkeypatch, provider, model, effort):
    requests = []
    events = await native_turn(monkeypatch, provider, model, effort, requests)
    assert events[-1].payload.state == "success", events[-1].payload.error
    assert events[0].payload.reasoning_effort == effort
    assert len(requests) == 1
    assert native_effort(provider, requests[0]) == effort


@pytest.mark.parametrize(
    ("provider", "model"), [("openai", "gpt-5"), ("anthropic", "claude-sonnet-5"), ("gemini", "gemini-3.5-flash")]
)
async def test_default_reaches_the_native_request_field_as_medium(monkeypatch, provider, model):
    requests = []
    async with socket_server(provider_service(provider, requests)) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with (
            await create_agent(AgentOptions(provider=provider, model=model)) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")]))
            events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success", events[-1].payload.error
    assert events[0].payload.reasoning_effort == "medium"
    assert native_effort(provider, requests[0]) == "medium"


async def test_model_without_reasoning_effort_sends_none(monkeypatch):
    requests = []
    events = await native_turn(monkeypatch, "openai", "gpt-4.1", "high", requests)
    assert events[-1].payload.state == "success", events[-1].payload.error
    assert events[0].payload.reasoning_effort is None
    assert "reasoning" not in requests[0]


@pytest.mark.parametrize(("model", "effort"), [("gemini-3.5-flash", "xhigh"), ("gemini-3.5-flash", "none")])
async def test_gemini_label_without_a_matching_level_is_rejected_before_a_request(monkeypatch, model, effort):
    requests = []
    with pytest.raises(AgentError) as caught:
        await native_turn(monkeypatch, "gemini", model, effort, requests)
    assert caught.value.code == "selector_rejected"
    assert requests == []


async def test_provider_refusal_of_a_sent_value_is_terminal_without_retry(monkeypatch):
    requests = []

    def refuse_effort(body):
        if "effort" in (body.get("reasoning") or {}):
            return "Unsupported value: 'reasoning.effort' is not supported with this model."
        return None

    events = await native_turn(monkeypatch, "openai", "gpt-5", "low", requests, request_validator=refuse_effort)
    result = events[-1].payload
    assert result.state == "failure"
    assert result.error.code == "provider_failed"
    assert len(requests) == 1
    assert requests[0]["reasoning"]["effort"] == "low"


async def chat_completions_turn(monkeypatch, agent_effort, turn_effort):
    requests = []
    async with socket_server(compatible_service("chat", requests)) as url:
        monkeypatch.setenv("CHAT_COMPLETIONS_BASE_URL", url + "/v1")
        monkeypatch.setenv("CHAT_COMPLETIONS_API_KEY", "fixture-api-key")
        async with (
            await create_agent(
                AgentOptions(provider="chat-completions", model="fixture-model", reasoning_effort=agent_effort)
            ) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")], reasoning_effort=turn_effort))
            events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success", events[-1].payload.error
    return events, requests


@pytest.mark.parametrize(("agent_effort", "turn_effort", "sent"), [(None, "low", "low"), ("high", None, "high")])
async def test_chat_completions_sends_a_named_value_as_a_top_level_field(monkeypatch, agent_effort, turn_effort, sent):
    events, requests = await chat_completions_turn(monkeypatch, agent_effort, turn_effort)
    assert events[0].payload.reasoning_effort == sent
    assert [body["reasoning_effort"] for body in requests] == [sent]


async def test_chat_completions_never_sends_the_default(monkeypatch):
    events, requests = await chat_completions_turn(monkeypatch, None, None)
    assert events[0].payload.reasoning_effort is None
    assert all("reasoning_effort" not in body for body in requests)


def copilot_fixture(monkeypatch):
    import copilot

    sessions = []

    class Session:
        session_id = "fixture-session"

        def __init__(self, config):
            self.config = config

        def on(self, handler):
            def adapted(event):
                handler(SimpleNamespace(type=event["type"], data=SimpleNamespace(**event["data"])))

            self.handler = adapted
            return lambda: None

        async def send(self, prompt, **kwargs):
            self.handler({"type": "assistant.message_delta", "data": {"delta_content": "Reply"}})
            self.handler({"type": "assistant.message", "data": {"content": "Reply"}})
            self.handler({"type": "assistant.usage", "data": {"input_tokens": 3, "output_tokens": 1}})
            self.handler({"type": "session.idle", "data": {}})
            return "fixture-message"

        async def abort(self):
            return None

        async def disconnect(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

        async def get_auth_status(self):
            return SimpleNamespace(isAuthenticated=True)

        async def create_session(self, **config):
            session = Session(config)
            sessions.append(session)
            return session

        async def stop(self):
            return None

        async def list_models(self):
            return []

    monkeypatch.setattr(copilot, "CopilotClient", Client)
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "fixture-token")
    return sessions


async def copilot_turn(monkeypatch, effort):
    sessions = copilot_fixture(monkeypatch)
    async with (
        await create_agent(AgentOptions(provider="github-copilot", model="gpt-5", tools=[])) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("Hello")], reasoning_effort=effort))
        events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success", events[-1].payload.error
    return events, sessions


async def test_copilot_sends_a_named_value_as_the_session_reasoning_effort(monkeypatch):
    events, sessions = await copilot_turn(monkeypatch, "low")
    assert events[0].payload.reasoning_effort == "low"
    assert [session.config.get("reasoning_effort") for session in sessions] == ["low"]


async def test_copilot_without_model_metadata_never_sends_the_default(monkeypatch):
    events, sessions = await copilot_turn(monkeypatch, None)
    assert events[0].payload.reasoning_effort is None
    assert all("reasoning_effort" not in session.config for session in sessions)


async def vllm_turn(monkeypatch, effort):
    requests = []
    async with socket_server(compatible_service("responses", requests)) as url:
        monkeypatch.setenv("VLLM_BASE_URL", url + "/v1")
        monkeypatch.setenv("VLLM_API_KEY", "fixture-api-key")
        async with (
            await create_agent(AgentOptions(provider="vllm", model="fixture-model")) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(TurnInput([TextPart("Hello")], reasoning_effort=effort))
            events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success", events[-1].payload.error
    return events, requests


async def test_vllm_sends_a_named_value_as_reasoning_effort(monkeypatch):
    events, requests = await vllm_turn(monkeypatch, "low")
    assert events[0].payload.reasoning_effort == "low"
    assert [body["reasoning"]["effort"] for body in requests] == ["low"]


async def test_vllm_never_sends_the_default(monkeypatch):
    events, requests = await vllm_turn(monkeypatch, None)
    assert events[0].payload.reasoning_effort is None
    assert all("effort" not in (body.get("reasoning") or {}) for body in requests)
