"""The default provider and model when no layer sets them (host-config/1 section 2)."""

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from amplifier_agent_engine._engine import discovery
from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._engine.provider_connections import CREDENTIAL_VARIABLES
from amplifier_agent_engine._records import AgentError, AgentOptions, DiscoveryOptions
import pytest

from tests.support.credentials import DEFAULT_CREDENTIALS

NEVER_DEFAULTED = {
    "AZURE_OPENAI_API_KEY": "fixture",
    "AZURE_OPENAI_ENDPOINT": "https://fixture.openai.azure.com",
    "OLLAMA_API_KEY": "fixture",
    "OLLAMA_HOST": "http://localhost:11434",
    "VLLM_API_KEY": "fixture",
    "VLLM_BASE_URL": "http://localhost:8000/v1",
    "CHAT_COMPLETIONS_API_KEY": "fixture",
    "CHAT_COMPLETIONS_BASE_URL": "http://localhost:9/v1",
}


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name in DEFAULT_CREDENTIALS or name in NEVER_DEFAULTED or name.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(name)
    config = tmp_path / "config.json"
    config.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(config))
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    # The real GitHub CLI on a developer machine may hold a login.
    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: None)
    return path


def chatgpt_login(home):
    path = home / ".amplifier" / "openai-chatgpt-oauth.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "account_id": "account-fixture",
                "expires_at": "2999-01-01T00:00:00+00:00",
            }
        )
    )


def selection(**environment):
    resolved = resolve(AgentOptions(environment=environment))
    return resolved.provider, resolved.model


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        (
            {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k", "GOOGLE_API_KEY": "k", "GH_TOKEN": "k"},
            ("openai", "gpt-6.1-sol"),
        ),
        ({"ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "k", "GH_TOKEN": "k"}, ("anthropic", "claude-sonnet-5-5")),
        ({"GEMINI_API_KEY": "k", "GH_TOKEN": "k"}, ("gemini", "gemini-3.8-flash")),
        ({"GOOGLE_API_KEY": "k"}, ("gemini", "gemini-3.8-flash")),
        ({"COPILOT_GITHUB_TOKEN": "k"}, ("github-copilot", "claude-sonnet-5-5")),
        ({"GITHUB_TOKEN": "k", **NEVER_DEFAULTED}, ("github-copilot", "claude-sonnet-5-5")),
    ],
)
def test_the_first_provider_with_credentials_is_the_default(environment, expected):
    assert selection(**environment) == expected


def test_a_chatgpt_login_precedes_github_copilot(home):
    chatgpt_login(home)
    assert selection(GH_TOKEN="k") == ("openai-chatgpt", "gpt-5.5")
    assert selection(GEMINI_API_KEY="k") == ("gemini", "gemini-3.8-flash")


def test_a_github_cli_login_counts_for_github_copilot(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b"gho_fixture\n")

    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: "/usr/bin/gh")
    monkeypatch.setattr(subprocess, "run", run)
    assert selection() == ("github-copilot", "claude-sonnet-5-5")
    assert calls == [["/usr/bin/gh", "auth", "token"]]
    # Credentials earlier in the order never ask the GitHub CLI.
    assert selection(ANTHROPIC_API_KEY="k") == ("anthropic", "claude-sonnet-5-5")
    assert len(calls) == 1


def test_an_uninstalled_provider_is_never_the_default(monkeypatch):
    monkeypatch.setattr(discovery, "installed", lambda provider: provider != "github-copilot")
    with pytest.raises(AgentError) as caught:
        selection(GH_TOKEN="k")
    assert caught.value.code == "engine_unavailable"


def test_server_and_deployment_providers_are_never_the_default():
    with pytest.raises(AgentError) as caught:
        selection(**NEVER_DEFAULTED)
    assert caught.value.code == "engine_unavailable"


def test_without_credentials_construction_names_the_variables_and_the_provider_setting():
    with pytest.raises(AgentError) as caught:
        selection()
    error = caught.value
    assert (error.code, error.category) == ("engine_unavailable", "lifecycle")
    for provider in ("openai", "anthropic", "gemini", "github-copilot"):
        for variable in CREDENTIAL_VARIABLES[provider]:
            assert variable in error.remedy
    assert "provider" in error.remedy
    assert "AZURE_OPENAI_API_KEY" not in error.remedy


@pytest.mark.parametrize("layer", ["options", "environment", "file"])
def test_a_configured_provider_skips_credential_discovery(monkeypatch, layer):
    options = AgentOptions()
    if layer == "options":
        options.provider = "gemini"
    elif layer == "environment":
        monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "gemini")
    else:
        Path(os.environ["AMPLIFIER_AGENT_CONFIG"]).write_text('{"provider": "gemini"}')
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    resolved = resolve(options)
    assert (resolved.provider, resolved.model) == ("gemini", "gemini-3.8-flash")


def test_a_configured_model_is_kept_with_the_default_provider():
    resolved = resolve(AgentOptions(model="gpt-5.5", environment={"OPENAI_API_KEY": "k"}))
    assert (resolved.provider, resolved.model) == ("openai", "gpt-5.5")


@pytest.mark.parametrize("provider", ["azure-openai", "ollama", "vllm", "chat-completions"])
def test_a_provider_without_a_default_model_needs_model(provider):
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions(provider=provider))
    assert caught.value.code == "invalid_input"
    assert caught.value.details == {"field": "model"}
    assert "model" in caught.value.remedy
    resolved = resolve(AgentOptions(provider=provider, model="fixture-model"))
    assert (resolved.provider, resolved.model) == (provider, "fixture-model")


async def test_gpt_6_lists_the_window_a_session_admits(monkeypatch):
    from openai.resources.models import AsyncModels

    async def listed(self, *args, **kwargs):
        return SimpleNamespace(data=[SimpleNamespace(id=name) for name in ("gpt-6.1-sol", "gpt-6-astra", "gpt-5.4")])

    monkeypatch.setattr(AsyncModels, "list", listed)
    records = await discovery.list_models("openai", DiscoveryOptions(environment={"OPENAI_API_KEY": "k"}))
    windows = {record.id: record.context_window for record in records}
    # GPT-6 sessions budget past the 272K standard-price tier, up to the module's full window.
    assert windows["gpt-6.1-sol"] == 922_000
    assert windows["gpt-6-astra"] == 922_000
    # Sessions keep other models within that tier, so their listing does too.
    assert windows["gpt-5.4"] == 272_000
