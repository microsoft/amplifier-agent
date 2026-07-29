"""Credential-resolution convergence (Phase 1) -- the canonical resolver.

Before this pass, THREE call sites (build_provider_entry's now-deleted
``_resolve_env_credential``, ``admin.models._resolve_provider_credentials``,
and the HTTP serve lifespan) independently re-implemented the env->file
precedence chain, and disagreed: ``models._resolve_provider_credentials``
was env-ONLY (no file fallback), so ``run`` (file-aware) and ``models list``
/ serve startup (env-only) would resolve the SAME credentials.json entry
differently.

These tests prove all three entry points -- ``run``'s ``build_provider_entry``,
``models list``'s ``list_provider_models``, and serve's
``enumerate_resolvable_providers`` -- now resolve identically through the one
canonical :func:`resolve_credential_detailed` chain.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.provider_env import clear_provider_env


@pytest.fixture(autouse=True)
def _isolated_credential_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test from BOTH the host shell's env vars AND any real
    ``~/.amplifier-agent/credentials.json`` on the machine running the suite.

    Without the ``AMPLIFIER_AGENT_HOME`` redirect, a real credentials file
    (e.g. one created by a developer running ``auth set`` locally) silently
    leaks into resolution tests that expect "nothing configured" -- so this
    fixture is autouse, not opt-in, for every test in this module.

    The env-var set comes from ``tests.provider_env`` (derived from
    ``PROVIDER_CREDENTIAL_VARS``) so adding a provider cannot leave a hole
    here.
    """
    clear_provider_env(monkeypatch)
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def credentials_home(_isolated_credential_environment: Path) -> Path:
    """Alias for the isolated home dir, for tests that want to write a
    credentials.json into it."""
    return _isolated_credential_environment


def _write_credentials(home: Path, providers: dict[str, dict[str, str]]) -> None:
    payload = {"version": 1, "providers": providers}
    (home / "credentials.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) Parity: run / models list / serve enumeration must agree
# ---------------------------------------------------------------------------


def test_resolve_from_file_key_providers(credentials_home: Path) -> None:
    """resolve_provider_credentials reads api_key from credentials.json for key providers."""
    from amplifier_agent_cli.provider_sources import resolve_provider_credentials

    _write_credentials(credentials_home, {"anthropic": {"api_key": "sk-file"}})

    creds = resolve_provider_credentials("anthropic")
    assert creds.get("api_key") == "sk-file"


def test_run_path_uses_file_credential(credentials_home: Path) -> None:
    """build_provider_entry (the `run` code path) resolves credentials.json when env is unset."""
    from amplifier_agent_cli.provider_sources import build_provider_entry

    _write_credentials(credentials_home, {"anthropic": {"api_key": "sk-file"}})

    entry = build_provider_entry("anthropic")
    assert entry["config"]["api_key"] == "sk-file"


def test_models_list_uses_file_credential(credentials_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_provider_models (the `models list` code path) must NOT raise when only
    credentials.json (not env) supplies the credential -- this was THE bug: the old
    ``admin.models._resolve_provider_credentials`` was env-only."""
    from amplifier_agent_cli.admin import models as models_module

    _write_credentials(credentials_home, {"anthropic": {"api_key": "sk-file"}})

    class _FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def list_models(self) -> list[object]:
            return []

    monkeypatch.setattr(models_module, "_load_provider_module", lambda provider_id: object())
    monkeypatch.setattr(models_module, "load_provider_class", lambda provider_id: _FakeProvider)

    # Must not raise ProviderCredentialsMissingError.
    result = models_module.list_provider_models("anthropic")
    assert result == []


def test_serve_enumeration_uses_file_credential(credentials_home: Path) -> None:
    """enumerate_resolvable_providers (the `serve` startup auto-enable path) sees the
    file-only credential too."""
    from amplifier_agent_cli.provider_sources import enumerate_resolvable_providers

    _write_credentials(credentials_home, {"anthropic": {"api_key": "sk-file"}})

    assert enumerate_resolvable_providers() == ["anthropic"]


def test_all_three_entry_points_resolve_identically(credentials_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same credentials.json entry produces the identical api_key string across
    build_provider_entry, list_provider_models's credential resolution, and
    enumerate_resolvable_providers."""
    from amplifier_agent_cli.admin import models as models_module
    from amplifier_agent_cli.provider_sources import (
        build_provider_entry,
        enumerate_resolvable_providers,
        resolve_provider_credentials,
    )

    _write_credentials(credentials_home, {"anthropic": {"api_key": "sk-shared-value"}})

    run_value = build_provider_entry("anthropic")["config"]["api_key"]
    models_value = resolve_provider_credentials("anthropic", required=True)["api_key"]
    assert "anthropic" in enumerate_resolvable_providers()

    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def list_models(self) -> list[object]:
            return []

    monkeypatch.setattr(models_module, "_load_provider_module", lambda provider_id: object())
    monkeypatch.setattr(models_module, "load_provider_class", lambda provider_id: _FakeProvider)
    models_module.list_provider_models("anthropic")

    assert run_value == models_value == "sk-shared-value"
    assert captured.get("api_key") == "sk-shared-value"


# ---------------------------------------------------------------------------
# (c) Ollama-specific resolution
# ---------------------------------------------------------------------------


def test_ollama_host_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """OLLAMA_HOST wins over OLLAMA_BASE_URL when both are set."""
    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    monkeypatch.setenv("OLLAMA_HOST", "http://from-host:11434")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://from-base-url:11434")

    resolution = resolve_credential_detailed("ollama")
    assert resolution.resolved is True
    assert resolution.source == "env"
    assert resolution.env_var == "OLLAMA_HOST"
    assert resolution.fields == {"host": "http://from-host:11434"}


def test_ollama_base_url_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """OLLAMA_BASE_URL is honoured when OLLAMA_HOST is unset."""
    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://from-base-url:11434")

    resolution = resolve_credential_detailed("ollama")
    assert resolution.resolved is True
    assert resolution.source == "env"
    assert resolution.env_var == "OLLAMA_BASE_URL"
    assert resolution.fields == {"host": "http://from-base-url:11434"}


def test_ollama_default_not_resolvable() -> None:
    """With no env and no credentials.json entry, ollama resolves to the built-in
    localhost default but is EXCLUDED from enumerate_resolvable_providers (source
    == "default" doesn't count as explicitly configured)."""
    from amplifier_agent_cli.provider_sources import enumerate_resolvable_providers, resolve_credential_detailed

    resolution = resolve_credential_detailed("ollama")
    assert resolution.resolved is False
    assert resolution.source == "default"
    assert resolution.fields == {"host": "http://localhost:11434"}
    assert "ollama" not in enumerate_resolvable_providers()


def test_ollama_file_entry_enables(credentials_home: Path) -> None:
    """A credentials.json host entry for ollama resolves and enables auto-enumeration."""
    from amplifier_agent_cli.provider_sources import enumerate_resolvable_providers, resolve_credential_detailed

    _write_credentials(credentials_home, {"ollama": {"host": "http://ollama.example.com:11434"}})

    resolution = resolve_credential_detailed("ollama")
    assert resolution.resolved is True
    assert resolution.source == "file"
    assert resolution.fields == {"host": "http://ollama.example.com:11434"}
    assert "ollama" in enumerate_resolvable_providers()


def test_ollama_run_mount_uses_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_provider_entry ships {"host": ...} for ollama -- NOT {"api_key": ...}.

    Regression test for the ollama mount-shape decision (Phase 1 spec section 2,
    site #5): the ollama provider module's own config reader consumes
    ``config["host"]``, so shipping the value under ``api_key`` would silently
    leave the provider unconfigured despite `build_provider_entry` reporting
    success.
    """
    from amplifier_agent_cli.provider_sources import build_provider_entry

    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    entry = build_provider_entry("ollama")

    assert entry["config"]["host"] == "http://localhost:11434"
    assert "api_key" not in entry["config"]


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_required_raises_when_absent() -> None:
    """resolve_provider_credentials(required=True) raises for a key provider with
    no resolvable credential."""
    from amplifier_agent_cli.provider_sources import (
        ProviderCredentialsMissingError,
        resolve_provider_credentials,
    )

    with pytest.raises(ProviderCredentialsMissingError):
        resolve_provider_credentials("anthropic", required=True)


def test_env_beats_file(credentials_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell env always wins over a persisted credentials.json entry."""
    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    _write_credentials(credentials_home, {"anthropic": {"api_key": "sk-file-value"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-value")

    resolution = resolve_credential_detailed("anthropic")
    assert resolution.source == "env"
    assert resolution.fields["api_key"] == "sk-env-value"


def test_azure_legacy_var_emits_notice(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """AZURE_OPENAI_KEY (legacy) resolves credentials AND emits a deprecation notice."""
    from amplifier_agent_cli import provider_sources
    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    provider_sources._LEGACY_ENV_VAR_NOTICE_EMITTED.discard("AZURE_OPENAI_KEY")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "sk-legacy-value")

    resolution = resolve_credential_detailed("azure-openai")
    assert resolution.source == "env"
    assert resolution.env_var == "AZURE_OPENAI_KEY"
    assert resolution.fields["api_key"] == "sk-legacy-value"

    captured = capsys.readouterr()
    assert "AZURE_OPENAI_KEY is deprecated" in captured.err


def test_credential_missing_error_importable_from_models() -> None:
    """ProviderCredentialsMissingError stays importable from admin.models for
    backward compatibility (amplifier_agent_http.app imports it from there)."""
    from amplifier_agent_cli.admin.models import ProviderCredentialsMissingError as FromModels
    from amplifier_agent_cli.provider_sources import ProviderCredentialsMissingError as FromSource

    assert FromModels is FromSource
