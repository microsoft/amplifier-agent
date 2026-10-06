"""Public provider discovery: top-level operations, public records, and public errors."""

import inspect
import os

import amplifier_agent
from amplifier_agent import AgentError, DiscoveryOptions, ModelRecord, ProviderRecord, list_models, list_providers
from amplifier_core.models import ModelInfo
import pytest

SECRET = "sentinel-credential-value"
NAMES = ("list_providers", "list_models", "DiscoveryOptions", "ProviderRecord", "ModelRecord")


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # The real GitHub CLI on a developer machine may hold a login.
    from amplifier_agent_engine._engine import discovery

    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: None)


def anthropic_models(monkeypatch, models):
    from amplifier_module_provider_anthropic import AnthropicProvider

    async def listed(self, *args, **kwargs):
        return list(models)

    monkeypatch.setattr(AnthropicProvider, "list_models", listed)


def test_discovery_is_exported_at_top_level():
    for name in NAMES:
        assert name in amplifier_agent.__all__, name
    assert inspect.iscoroutinefunction(list_providers)
    assert inspect.iscoroutinefunction(list_models)


async def test_providers_are_public_records_naming_variables_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    listed = await list_providers(DiscoveryOptions(environment={"ANTHROPIC_API_KEY": SECRET}))
    assert all(isinstance(record, ProviderRecord) for record in listed)
    by_name = {record.provider: record for record in listed}
    assert by_name["anthropic"].credentials == "found"
    assert by_name["openai"].credentials == "found"
    assert by_name["gemini"].credentials == "missing"
    assert "ANTHROPIC_API_KEY" in by_name["anthropic"].credential_variables
    assert SECRET not in repr(listed)
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert [record.provider for record in await list_providers()] == [record.provider for record in listed]


async def test_models_are_public_records(monkeypatch):
    anthropic_models(
        monkeypatch,
        [
            ModelInfo(id="claude-a", display_name="Claude A", context_window=200_000, max_output_tokens=64_000),
            ModelInfo(id="claude-b", display_name="Claude B", context_window=0, max_output_tokens=0),
        ],
    )
    listed = await list_models("anthropic", DiscoveryOptions(environment={"ANTHROPIC_API_KEY": SECRET}))
    assert listed == [
        ModelRecord(id="claude-a", display_name="Claude A", context_window=200_000, max_output_tokens=64_000),
        ModelRecord(id="claude-b", display_name="Claude B"),
    ]
    assert all(type(record) is ModelRecord for record in listed)
    assert (listed[1].context_window, listed[1].max_output_tokens) == (None, None)


@pytest.mark.parametrize(
    ("provider", "code", "remedy"),
    [("not-a-provider", "invalid_input", None), ("anthropic", "provider_failed", "ANTHROPIC_API_KEY")],
)
async def test_failures_are_public_agent_errors(provider, code, remedy):
    with pytest.raises(AgentError) as caught:
        await list_models(provider)
    assert type(caught.value) is AgentError
    assert caught.value.code == code
    assert caught.value.remedy
    if remedy is not None:
        assert remedy in caught.value.remedy
