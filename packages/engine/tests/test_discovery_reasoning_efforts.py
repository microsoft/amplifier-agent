"""ModelRecord.reasoning_efforts: what an agent selecting each listed model accepts."""

import os
from types import SimpleNamespace
from typing import Any

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
    ("openai", "gpt-6.1-sol", ["low", "medium", "high", "xhigh", "max"]),
    ("openai", "gpt-6.1-terra", ["none", "low", "medium", "high", "xhigh", "max"]),
    ("openai", "gpt-6.2-astra", ["low", "medium"]),
    ("openai", "gpt-6.2-sol", ["low", "medium"]),
    ("openai", "gpt-5.5", ["none", "low", "medium", "high", "xhigh"]),
    ("openai", "gpt-5.5-pro", ["medium", "high", "xhigh"]),
    ("openai", "gpt-5.5-pro-2026-04-23", ["medium", "high", "xhigh"]),
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


@pytest.mark.parametrize("provider", ["openai", "azure-openai"])
@pytest.mark.parametrize(
    ("allowed", "expected"),
    [
        (frozenset({"medium", "low", "provider-only-value"}), ("low", "medium")),
        (frozenset({"high"}), ("high",)),
        (frozenset(), ()),
        (None, reasoning.EFFORTS),
    ],
)
def test_openai_efforts_use_the_exact_provider_request_policy(monkeypatch, provider, allowed, expected):
    from amplifier_module_provider_openai import _capabilities

    calls = []

    def policy(model):
        calls.append(model)
        return _capabilities.RequestPolicy(allowed_efforts=allowed)

    monkeypatch.setattr(_capabilities, "get_capabilities", lambda model: SimpleNamespace(supports_reasoning=True))
    monkeypatch.setattr(_capabilities, "get_request_policy", policy)
    assert reasoning.support(provider, "provider-owned-model") == expected
    assert calls == ["provider-owned-model"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.5", reasoning.EFFORTS),
        ("gpt-6.2-sol", reasoning.EFFORTS),
        ("gpt-5.5-pro", ("medium", "high", "xhigh")),
        ("gpt-5.5-pro-2026-04-23", ("medium", "high", "xhigh")),
        ("gpt-4.1", ()),
    ],
)
def test_chatgpt_uses_its_own_effort_validation_not_the_api_registry(monkeypatch, model, expected):
    from amplifier_module_provider_openai import _capabilities

    def wrong_channel(model):
        raise AssertionError("ChatGPT must not consume OpenAI API request policy.")

    monkeypatch.setattr(_capabilities, "get_request_policy", wrong_channel)
    assert reasoning.support("openai-chatgpt", model) == expected
    for effort in reasoning.EFFORTS:
        if expected and effort not in expected:
            with pytest.raises(AgentError) as caught:
                reasoning.sent("openai-chatgpt", model, effort)
            assert caught.value.code == "selector_rejected"
        else:
            assert reasoning.sent("openai-chatgpt", model, effort) == (effort if expected else None)
    assert reasoning.sent("openai-chatgpt", model, None) == ("medium" if expected else None)


def test_nonreasoning_openai_models_take_no_effort_even_with_a_request_policy(monkeypatch):
    from amplifier_module_provider_openai import _capabilities

    monkeypatch.setattr(
        _capabilities,
        "get_request_policy",
        lambda model: _capabilities.RequestPolicy(allowed_efforts=frozenset({"medium"})),
    )
    assert reasoning.support("openai", "gpt-4.1") == ()


async def test_discovery_and_selection_use_a_new_models_exact_policy(monkeypatch):
    from amplifier_module_provider_openai import _capabilities

    model = "gpt-6.2-sol"
    capabilities = _capabilities.get_capabilities("gpt-6.1-sol")
    monkeypatch.setattr(_capabilities, "get_capabilities", lambda model: capabilities)
    monkeypatch.setattr(
        _capabilities,
        "get_request_policy",
        lambda model: _capabilities.RequestPolicy(allowed_efforts=frozenset({"low", "medium"})),
    )
    assert await listed_efforts(monkeypatch, "openai", model) == ["low", "medium"]
    monkeypatch.setattr(assembly, "_provider_factory", ScriptedFactory([]))
    for effort in reasoning.EFFORTS:
        options = AgentOptions(provider="openai", model=model, reasoning_effort=effort)
        if effort in {"low", "medium"}:
            await (await assembly.create_engine(options)).close()
        else:
            with pytest.raises(AgentError) as caught:
                await assembly.create_engine(options)
            assert caught.value.code == "selector_rejected"


async def test_copilot_lists_the_efforts_its_model_descriptions_report(monkeypatch):
    import copilot
    from copilot.generated.rpc import ServerModelsApi

    def described(model, supports, efforts=()):
        return {
            "id": model,
            "name": model,
            "capabilities": {"supports": {"reasoningEffort": supports}},
            "supportedReasoningEfforts": list(efforts),
        }

    descriptions = [
        described("gpt-5", True, ("low", "medium", "high")),
        described("gpt-4.1", False),
        described("advertised-nothing", True),
    ]

    async def request(method, params, **kwargs):
        assert method == "models.list"
        return {"models": descriptions}

    transport: Any = SimpleNamespace(request=request)

    class Client:
        rpc = SimpleNamespace(models=ServerModelsApi(transport))

        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

        async def get_auth_status(self):
            return SimpleNamespace(isAuthenticated=True)

        async def stop(self):
            return None

    monkeypatch.setattr(copilot, "CopilotClient", Client)
    records = await discovery.list_models("github-copilot", DiscoveryOptions(environment={"GH_TOKEN": "fixture-token"}))
    assert {record.id: record.reasoning_efforts for record in records} == {
        "gpt-5": ["low", "medium", "high"],
        "gpt-4.1": [],
        "advertised-nothing": None,
    }
