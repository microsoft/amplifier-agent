"""ModelRecord.reasoning_efforts: what an agent selecting each listed model accepts."""

import os
from types import SimpleNamespace

from amplifier_agent_engine._engine import assembly, discovery, reasoning
from amplifier_agent_engine._records import AgentError, AgentOptions, DiscoveryOptions
from amplifier_core.models import ModelInfo
import pytest

from tests.support.scripted_provider import ScriptedFactory

ENVIRONMENT = {
    "anthropic": {"ANTHROPIC_API_KEY": "fixture-key"},
    "openai": {"OPENAI_API_KEY": "fixture-key"},
    "gemini": {"GOOGLE_API_KEY": "fixture-key"},
    "chat-completions": {"CHAT_COMPLETIONS_BASE_URL": "http://127.0.0.1:9/v1"},
    "ollama": {"OLLAMA_HOST": "http://127.0.0.1:9"},
    "vllm": {"VLLM_BASE_URL": "http://127.0.0.1:9/v1"},
}
CASES = [
    ("anthropic", "claude-sonnet-5", ["low", "medium", "high", "xhigh", "max"]),
    ("anthropic", "claude-haiku-4-5", ["low", "medium", "high"]),
    ("openai", "gpt-5", list(reasoning.EFFORTS)),
    ("openai", "gpt-4.1", []),
    ("openai", "gpt-6-astra", ["low", "medium", "high", "xhigh", "max"]),
    ("openai", "gpt-5.5-pro", ["medium", "high", "xhigh"]),
    ("gemini", "gemini-3.5-flash", ["minimal", "low", "medium", "high"]),
    ("gemini", "gemini-3.7-flash", ["low", "medium", "high"]),
    ("gemini", "gemini-2.5-flash", []),
    ("chat-completions", "fixture-model", None),
    ("ollama", "fixture-model", None),
    ("vllm", "fixture-model", None),
]


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("AMPLIFIER_AGENT_") or any(name in values for values in ENVIRONMENT.values()):
            monkeypatch.delenv(name)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: None)


def listing(monkeypatch, models):
    async def listed(provider, instance):
        return [ModelInfo(id=model, display_name=model, context_window=1, max_output_tokens=1) for model in models]

    monkeypatch.setattr(discovery, "_models", listed)


async def listed_efforts(monkeypatch, provider, model):
    listing(monkeypatch, [model])
    (record,) = await discovery.list_models(provider, DiscoveryOptions(environment=ENVIRONMENT[provider]))
    return record.reasoning_efforts


@pytest.mark.parametrize(("provider", "model", "expected"), CASES)
async def test_listed_reasoning_efforts_follow_the_provider_module(monkeypatch, provider, model, expected):
    assert await listed_efforts(monkeypatch, provider, model) == expected


@pytest.mark.parametrize(("provider", "model", "expected"), CASES)
async def test_an_agent_accepts_exactly_the_listed_values(monkeypatch, provider, model, expected):
    listed = await listed_efforts(monkeypatch, provider, model)
    monkeypatch.setattr(assembly, "_provider_factory", ScriptedFactory([]))
    for value in reasoning.EFFORTS:
        options = AgentOptions(provider=provider, model=model, reasoning_effort=value)
        if listed and value not in listed:
            with pytest.raises(AgentError) as caught:
                await assembly.create_engine(options)
            assert caught.value.code == "selector_rejected"
        else:
            await (await assembly.create_engine(options)).close()


async def test_copilot_lists_the_efforts_its_model_descriptions_report(monkeypatch):
    from amplifier_module_provider_github_copilot import models
    from amplifier_module_provider_github_copilot.sdk_adapter.model_translation import CopilotModelInfo
    import copilot

    class Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

        async def get_auth_status(self):
            return SimpleNamespace(isAuthenticated=True)

        async def stop(self):
            return None

    def described(model, supports, efforts=()):
        return CopilotModelInfo(
            id=model,
            name=model,
            context_window=1,
            max_output_tokens=1,
            supports_reasoning_effort=supports,
            supported_reasoning_efforts=efforts,
        )

    descriptions = [
        described("gpt-5", True, ("low", "medium", "high")),
        described("gpt-4.1", False),
        described("advertised-nothing", True),
    ]

    async def fetched(client):
        listed = [
            ModelInfo(id=item.id, display_name=item.name, context_window=1, max_output_tokens=1)
            for item in descriptions
        ]
        return listed, descriptions

    monkeypatch.setattr(copilot, "CopilotClient", Client)
    monkeypatch.setattr(models, "fetch_and_map_models", fetched)
    records = await discovery.list_models("github-copilot", DiscoveryOptions(environment={"GH_TOKEN": "fixture-token"}))
    assert {record.id: record.reasoning_efforts for record in records} == {
        "gpt-5": ["low", "medium", "high"],
        "gpt-4.1": [],
        "advertised-nothing": None,
    }
