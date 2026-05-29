"""Tests for amplifier_agent_cli.provider_detect — auto-detect provider from env vars."""

from __future__ import annotations

import pytest

from amplifier_agent_cli.provider_detect import ProviderNotConfigured, detect_provider

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_KEY",  # legacy alias, still accepted
    "OLLAMA_HOST",
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider env var (including legacy aliases) before each test."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


def test_detects_anthropic_when_only_anthropic_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert detect_provider(None) == "anthropic"


def test_detects_openai_when_only_openai_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    assert detect_provider(None) == "openai"


def test_detects_azure_when_only_azure_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key-test")
    assert detect_provider(None) == "azure-openai"


def test_detects_azure_via_legacy_env_var(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Legacy AZURE_OPENAI_KEY still detects azure-openai (with deprecation warning)."""
    # Reset the per-process one-shot tracker so this test always sees the warning.
    from amplifier_agent_cli import provider_detect

    provider_detect._DEPRECATION_NOTICE_EMITTED.clear()
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az-key-legacy")
    assert detect_provider(None) == "azure-openai"
    captured = capsys.readouterr()
    assert "AZURE_OPENAI_KEY" in captured.err
    assert "deprecated" in captured.err.lower()
    assert "AZURE_OPENAI_API_KEY" in captured.err


def test_preferred_azure_wins_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """If both AZURE_OPENAI_API_KEY and AZURE_OPENAI_KEY are set, the preferred one wins."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "preferred")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "legacy")
    assert detect_provider(None) == "azure-openai"


def test_detects_ollama_when_only_ollama_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    assert detect_provider(None) == "ollama"


# ---------------------------------------------------------------------------
# Precedence tests
# ---------------------------------------------------------------------------


def test_precedence_anthropic_over_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    assert detect_provider(None) == "anthropic"


def test_precedence_openai_over_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az-key-test")
    assert detect_provider(None) == "openai"


def test_precedence_azure_over_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az-key-test")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    assert detect_provider(None) == "azure-openai"


# ---------------------------------------------------------------------------
# Override tests
# ---------------------------------------------------------------------------


def test_override_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """--provider override should win even when ANTHROPIC_API_KEY is set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert detect_provider("openai") == "openai"


def test_override_accepts_known_providers() -> None:
    """All four known provider names must be accepted as overrides."""
    for provider in ("anthropic", "openai", "azure-openai", "ollama"):
        assert detect_provider(provider) == provider


def test_override_rejects_unknown_provider() -> None:
    """An unknown --provider value must raise ProviderNotConfigured."""
    with pytest.raises(ProviderNotConfigured) as exc_info:
        detect_provider("bogus")
    err = exc_info.value
    assert err.code == "provider_not_configured"
    assert "bogus" in err.message


# ---------------------------------------------------------------------------
# No-provider error test
# ---------------------------------------------------------------------------


def test_raises_when_no_env_and_no_override() -> None:
    """When no env var is set and no override given, raise ProviderNotConfigured."""
    with pytest.raises(ProviderNotConfigured) as exc_info:
        detect_provider(None)
    err = exc_info.value
    assert err.code == "provider_not_configured"
    assert "ANTHROPIC_API_KEY" in err.message
