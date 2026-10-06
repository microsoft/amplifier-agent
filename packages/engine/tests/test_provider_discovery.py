"""Provider discovery: credential status, record mapping, and listing that fails loud."""

import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

from amplifier_agent_engine._engine import discovery
from amplifier_agent_engine._engine.provider_policy import PROVIDERS
from amplifier_agent_engine._records import AgentError, DiscoveryOptions, ModelRecord, ProviderRecord
from amplifier_core.llm_errors import AuthenticationError
from amplifier_core.models import ModelInfo
import httpx
import openai
import pytest

SECRET = "sentinel-credential-value"
UNREACHABLE = "http://127.0.0.1:9"
HOST_VARIABLES = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "OLLAMA_API_KEY",
    "OLLAMA_HOST",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GEMINI_BASE_URL",
    "VLLM_API_KEY",
    "VLLM_BASE_URL",
    "CHAT_COMPLETIONS_API_KEY",
    "CHAT_COMPLETIONS_BASE_URL",
    "COPILOT_AGENT_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
}


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name in HOST_VARIABLES or name.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(name)
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    # The real GitHub CLI on a developer machine may hold a login.
    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: None)
    return path


def options(**environment: str) -> DiscoveryOptions:
    return DiscoveryOptions(environment=environment)


async def providers(**environment: str) -> dict:
    return {record.provider: record for record in await discovery.list_providers(options(**environment))}


async def refusal(provider: str, **environment: str) -> AgentError:
    with pytest.raises(AgentError) as caught:
        await asyncio.wait_for(discovery.list_models(provider, options(**environment)), 10)
    assert caught.value.remedy
    return caught.value


def anthropic_listing(monkeypatch, listed):
    from amplifier_module_provider_anthropic import AnthropicProvider

    monkeypatch.setattr(AnthropicProvider, "list_models", listed)


def write_chatgpt_tokens(home):
    path = home / ".amplifier" / "openai-chatgpt-oauth.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "access_token": SECRET,
                "refresh_token": SECRET,
                "account_id": "account-fixture",
                "expires_at": "2999-01-01T00:00:00+00:00",
            }
        )
    )


async def test_every_accepted_provider_is_listed_in_a_stable_order():
    first = await discovery.list_providers()
    second = await discovery.list_providers()
    assert [record.provider for record in first] == [record.provider for record in second]
    assert len(first) == len(PROVIDERS)
    assert {record.provider for record in first} == PROVIDERS
    for record in first:
        assert isinstance(record, ProviderRecord)
        assert record.display_name
        assert isinstance(record.installed, bool)
        assert record.credentials in {"found", "missing", "not_required"}
        assert all(isinstance(name, str) and name for name in record.credential_variables)


async def test_credential_status_names_variables_and_never_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    listed = await providers(GEMINI_API_KEY=SECRET)
    assert listed["openai"].credentials == "found"
    assert listed["gemini"].credentials == "found"
    assert listed["anthropic"].credentials == "missing"
    assert "ANTHROPIC_API_KEY" in listed["anthropic"].credential_variables
    assert "OPENAI_API_KEY" in listed["openai"].credential_variables
    assert {"GOOGLE_API_KEY", "GEMINI_API_KEY"} <= set(listed["gemini"].credential_variables)
    assert SECRET not in repr(list(listed.values()))


async def test_environment_applies_over_the_host_without_changing_it():
    before = dict(os.environ)
    listed = await providers(ANTHROPIC_API_KEY=SECRET)
    assert listed["anthropic"].credentials == "found"
    assert dict(os.environ) == before
    assert (await providers())["anthropic"].credentials == "missing"


@pytest.mark.parametrize("host", [None, "http://localhost:11434", "http://127.0.0.1:11434"])
async def test_ollama_on_a_local_host_needs_no_credential(host):
    listed = await providers(**({} if host is None else {"OLLAMA_HOST": host}))
    assert listed["ollama"].credentials == "not_required"


async def test_openai_chatgpt_credentials_are_its_token_file(home):
    assert (await providers())["openai-chatgpt"].credentials == "missing"
    write_chatgpt_tokens(home)
    listed = await providers()
    assert listed["openai-chatgpt"].credentials == "found"
    assert SECRET not in repr(list(listed.values()))


async def test_discovery_reads_no_host_configuration(monkeypatch, tmp_path):
    broken = tmp_path / "host.json"
    broken.write_text("{not json")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(broken))
    monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "not-a-provider")
    assert len(await discovery.list_providers()) == len(PROVIDERS)


async def test_invalid_environment_is_refused_as_agent_options_refuse_it():
    unchecked: Any = {"NAME": 1}
    with pytest.raises(AgentError) as caught:
        await discovery.list_providers(DiscoveryOptions(environment={"": "value"}))
    assert caught.value.code == "invalid_input"
    with pytest.raises(AgentError) as caught:
        await discovery.list_models("anthropic", DiscoveryOptions(environment=unchecked))
    assert caught.value.code == "invalid_input"


async def test_models_map_to_records_with_unreported_limits_absent(monkeypatch):
    reported = [
        ModelInfo(
            id="claude-a",
            display_name="Claude A",
            context_window=200_000,
            max_output_tokens=64_000,
            capabilities=["tools"],
            defaults={"temperature": 1},
        ),
        ModelInfo(id="claude-b", display_name="Claude B", context_window=0, max_output_tokens=0),
        ModelInfo.model_construct(id="claude-c", display_name="Claude C", context_window=None, max_output_tokens=None),
    ]

    async def listed(self, *args, **kwargs):
        return list(reported)

    anthropic_listing(monkeypatch, listed)
    model = ModelRecord
    assert await discovery.list_models("anthropic", options(ANTHROPIC_API_KEY=SECRET)) == [
        model(id="claude-a", display_name="Claude A", context_window=200_000, max_output_tokens=64_000),
        model(id="claude-b", display_name="Claude B"),
        model(id="claude-c", display_name="Claude C"),
    ]
    absent = model(id="claude-b", display_name="Claude B")
    assert (absent.context_window, absent.max_output_tokens) == (None, None)


async def test_every_listing_asks_the_provider_again(monkeypatch):
    calls = []

    async def listed(self, *args, **kwargs):
        calls.append(self)
        return [ModelInfo(id=f"claude-{len(calls)}", display_name="Claude", context_window=1, max_output_tokens=1)]

    anthropic_listing(monkeypatch, listed)
    first = await discovery.list_models("anthropic", options(ANTHROPIC_API_KEY=SECRET))
    second = await discovery.list_models("anthropic", options(ANTHROPIC_API_KEY=SECRET))
    assert [model.id for model in first + second] == ["claude-1", "claude-2"]


async def test_unknown_provider_is_invalid_input():
    assert (await refusal("not-a-provider")).code == "invalid_input"


async def test_missing_credentials_fail_before_asking_the_provider(monkeypatch):
    async def listed(self, *args, **kwargs):
        raise AssertionError("A provider without credentials was asked for models.")

    anthropic_listing(monkeypatch, listed)
    error = await refusal("anthropic")
    assert error.code == "provider_failed"
    assert "ANTHROPIC_API_KEY" in error.remedy


async def test_rejected_credentials_name_the_variable(monkeypatch):
    async def listed(self, *args, **kwargs):
        raise AuthenticationError("invalid x-api-key", provider="anthropic", status_code=401)

    anthropic_listing(monkeypatch, listed)
    error = await refusal("anthropic", ANTHROPIC_API_KEY=SECRET)
    assert error.code == "provider_failed"
    assert "ANTHROPIC_API_KEY" in error.remedy
    assert SECRET not in f"{error.message} {error.remedy} {error.details}"


async def test_timeout_is_a_retryable_provider_failure(monkeypatch):
    monkeypatch.setattr(discovery, "LIST_MODELS_SECONDS", 0.05)

    async def listed(self, *args, **kwargs):
        await asyncio.Event().wait()

    anthropic_listing(monkeypatch, listed)
    error = await refusal("anthropic", ANTHROPIC_API_KEY=SECRET)
    assert error.code == "provider_failed"
    assert error.retryable is True


async def test_ollama_listing_failure_is_not_an_empty_list(monkeypatch):
    from ollama import AsyncClient

    async def unreachable(self, *args, **kwargs):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(AsyncClient, "list", unreachable)
    error = await refusal("ollama", OLLAMA_HOST=UNREACHABLE)
    assert error.code == "provider_failed"
    assert "OLLAMA_HOST" in error.remedy or UNREACHABLE in error.remedy


async def test_ollama_without_models_lists_none(monkeypatch):
    from ollama import AsyncClient, ListResponse

    async def empty(self, *args, **kwargs):
        return ListResponse(models=[])

    monkeypatch.setattr(AsyncClient, "list", empty)
    assert await discovery.list_models("ollama", options(OLLAMA_HOST=UNREACHABLE)) == []


async def test_openai_chatgpt_fallback_catalog_is_a_failure(monkeypatch, home):
    from amplifier_module_provider_openai_chatgpt import models, provider

    async def unreachable(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(models, "fetch_models", unreachable)
    monkeypatch.setattr(provider, "fetch_models", unreachable)
    write_chatgpt_tokens(home)
    assert (await refusal("openai-chatgpt")).code == "provider_failed"


async def test_openai_chatgpt_without_tokens_is_a_failure():
    assert (await refusal("openai-chatgpt")).code == "provider_failed"


async def test_chat_completions_configured_model_fallback_is_a_failure(monkeypatch):
    from openai.resources.models import AsyncModels

    def unreachable(self, *args, **kwargs):
        raise openai.APIConnectionError(request=httpx.Request("GET", f"{UNREACHABLE}/v1/models"))

    monkeypatch.setattr(AsyncModels, "list", unreachable)
    error = await refusal(
        "chat-completions",
        CHAT_COMPLETIONS_BASE_URL=f"{UNREACHABLE}/v1",
        CHAT_COMPLETIONS_API_KEY=SECRET,
    )
    assert error.code == "provider_failed"
    assert "CHAT_COMPLETIONS_BASE_URL" in error.remedy or UNREACHABLE in error.remedy


async def test_deployment_providers_list_no_models():
    listed = await discovery.list_models(
        "azure-openai",
        options(AZURE_OPENAI_API_KEY=SECRET, AZURE_OPENAI_ENDPOINT="https://fixture.openai.azure.com"),
    )
    assert listed == []


async def test_copilot_without_its_extra_is_not_installed(monkeypatch):
    for name in list(sys.modules):
        for package in ("amplifier_module_provider_github_copilot", "copilot"):
            if name == package or name.startswith(package + "."):
                monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "amplifier_module_provider_github_copilot", None)
    monkeypatch.setitem(sys.modules, "copilot", None)
    listed = await providers()
    assert listed["github-copilot"].installed is False
    assert all(record.installed for name, record in listed.items() if name != "github-copilot")
    error = await refusal("github-copilot", GH_TOKEN=SECRET)
    assert error.code == "engine_unavailable"
    assert "github-copilot" in error.remedy


@pytest.mark.parametrize(
    ("provider", "environment", "credentials"),
    [
        ("vllm", {}, "not_required"),
        ("vllm", {"VLLM_BASE_URL": "https://vllm.example/v1"}, "not_required"),
        ("vllm", {"VLLM_API_KEY": SECRET}, "found"),
        ("chat-completions", {}, "not_required"),
        ("chat-completions", {"CHAT_COMPLETIONS_BASE_URL": "https://chat.example/v1"}, "not_required"),
        ("chat-completions", {"CHAT_COMPLETIONS_API_KEY": SECRET}, "found"),
        ("azure-openai", {}, "missing"),
        ("azure-openai", {"AZURE_OPENAI_API_KEY": SECRET}, "missing"),
        ("azure-openai", {"AZURE_OPENAI_ENDPOINT": "https://fixture.openai.azure.com"}, "missing"),
        (
            "azure-openai",
            {"AZURE_OPENAI_API_KEY": SECRET, "AZURE_OPENAI_ENDPOINT": "https://fixture.openai.azure.com"},
            "found",
        ),
        ("ollama", {"OLLAMA_HOST": "https://ollama.com"}, "missing"),
        ("ollama", {"OLLAMA_HOST": "https://ollama.com", "OLLAMA_API_KEY": SECRET}, "found"),
        ("github-copilot", {}, "missing"),
        ("github-copilot", {"COPILOT_AGENT_TOKEN": SECRET}, "found"),
        ("github-copilot", {"COPILOT_GITHUB_TOKEN": SECRET}, "found"),
        ("github-copilot", {"GH_TOKEN": SECRET}, "found"),
        ("github-copilot", {"GITHUB_TOKEN": SECRET}, "found"),
    ],
)
async def test_credential_status_per_provider(provider, environment, credentials):
    listed = await providers(**environment)
    assert listed[provider].credentials == credentials
    assert SECRET not in repr(listed[provider])


@pytest.mark.parametrize(
    ("provider", "variables"),
    [
        ("anthropic", {"ANTHROPIC_API_KEY"}),
        ("openai", {"OPENAI_API_KEY"}),
        ("azure-openai", {"AZURE_OPENAI_API_KEY"}),
        ("ollama", {"OLLAMA_API_KEY"}),
        ("gemini", {"GOOGLE_API_KEY", "GEMINI_API_KEY"}),
        ("vllm", {"VLLM_API_KEY"}),
        ("chat-completions", {"CHAT_COMPLETIONS_API_KEY"}),
        ("github-copilot", {"COPILOT_AGENT_TOKEN", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}),
        ("openai-chatgpt", set()),
    ],
)
async def test_credential_variables_name_credentials_not_endpoints(provider, variables):
    assert set((await providers())[provider].credential_variables) == variables


async def test_copilot_without_token_variables_asks_the_provider(monkeypatch):
    from amplifier_agent_engine._engine import providers
    from amplifier_agent_engine._engine.provider_connections import require

    asked = []

    async def unauthenticated(config, coordinator, params):
        asked.append(config.provider)
        require(False, config.provider, "Set a Copilot token or complete SDK login before constructing the agent.")

    monkeypatch.setattr(providers, "_copilot", unauthenticated)
    error = await refusal("github-copilot")
    assert asked == ["github-copilot"]
    assert error.code == "provider_failed"
    assert "gh auth login" in error.remedy
    assert "COPILOT_AGENT_TOKEN" in error.remedy


async def test_azure_with_only_an_endpoint_lists_no_models():
    listed = await discovery.list_models(
        "azure-openai", options(AZURE_OPENAI_ENDPOINT="https://fixture.openai.azure.com")
    )
    assert listed == []


async def test_azure_without_an_endpoint_names_it():
    error = await refusal("azure-openai", AZURE_OPENAI_API_KEY=SECRET)
    assert error.code == "provider_failed"
    assert "AZURE_OPENAI_ENDPOINT" in error.remedy


TOKEN_OUTPUT = SECRET.encode() + b"\n"


class FakeGh:
    """Stands in for `gh auth token` with a scripted outcome."""

    def __init__(self, returncode=0, output=TOKEN_OUTPUT, hang=False):
        self.returncode = None
        self._exit = returncode
        self._output = output
        self._hang = hang
        self.calls = []
        self.killed = False

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self

    async def communicate(self):
        if self._hang:
            await asyncio.Event().wait()
        self.returncode = self._exit
        return self._output, None

    def kill(self):
        self.killed = True

    async def wait(self):
        self.returncode = -9
        return self.returncode


def gh(monkeypatch, fake):
    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: "/usr/bin/gh")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return fake


async def test_copilot_github_cli_login_is_found(monkeypatch):
    fake = gh(monkeypatch, FakeGh())
    listed = await providers(GH_CONFIG_DIR="/fixture/gh")
    assert listed["github-copilot"].credentials == "found"
    assert SECRET not in repr(list(listed.values()))
    ((args, kwargs),) = fake.calls
    assert args == ("/usr/bin/gh", "auth", "token")
    assert kwargs["env"]["GH_CONFIG_DIR"] == "/fixture/gh"
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE


async def test_copilot_without_the_github_cli_is_missing():
    assert (await providers())["github-copilot"].credentials == "missing"


@pytest.mark.parametrize("fake", [FakeGh(returncode=1, output=b""), FakeGh(returncode=0, output=b"  \n")])
async def test_copilot_without_a_github_cli_token_is_missing(monkeypatch, fake):
    gh(monkeypatch, fake)
    assert (await providers())["github-copilot"].credentials == "missing"


async def test_copilot_github_cli_timeout_is_missing(monkeypatch):
    monkeypatch.setattr(discovery, "GH_AUTH_SECONDS", 0.05)
    fake = gh(monkeypatch, FakeGh(hang=True))
    assert (await providers())["github-copilot"].credentials == "missing"
    assert fake.killed


async def test_a_token_variable_skips_the_github_cli(monkeypatch):
    fake = gh(monkeypatch, FakeGh(returncode=1))
    assert (await providers(GH_TOKEN=SECRET))["github-copilot"].credentials == "found"
    assert fake.calls == []


async def test_keyless_chat_completions_asks_the_server(monkeypatch):
    from openai.resources.models import AsyncModels

    asked = []

    async def listed(self, *args, **kwargs):
        asked.append(self._client.api_key)
        return SimpleNamespace(data=[SimpleNamespace(id="local-model", object="model")])

    monkeypatch.setattr(AsyncModels, "list", listed)
    models = await discovery.list_models("chat-completions", options(CHAT_COMPLETIONS_BASE_URL="http://127.0.0.1:9/v1"))
    assert len(asked) == 1
    assert [model.id for model in models] == ["local-model"]


async def test_chat_completions_key_rejection_names_the_variable(monkeypatch):
    from openai.resources.models import AsyncModels

    def rejected(self, *args, **kwargs):
        request = httpx.Request("GET", f"{UNREACHABLE}/v1/models")
        response = httpx.Response(401, request=request)
        raise openai.AuthenticationError("API key required", response=response, body=None)

    monkeypatch.setattr(AsyncModels, "list", rejected)
    error = await refusal("chat-completions", CHAT_COMPLETIONS_BASE_URL=f"{UNREACHABLE}/v1")
    assert error.code == "provider_failed"
    assert "CHAT_COMPLETIONS_API_KEY" in error.remedy


async def test_chat_completions_without_an_endpoint_names_it():
    error = await refusal("chat-completions")
    assert error.code == "provider_failed"
    assert "CHAT_COMPLETIONS_BASE_URL" in error.remedy
