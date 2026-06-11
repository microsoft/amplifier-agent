"""Tests for amplifier_agent_cli.provider_sources.

The catalog maps the short provider names (anthropic / openai / azure-openai /
ollama) resolved from config / bundle.md ``default_provider`` (D6) to the
module URI and env-var template needed to mount that provider via foundation's
``mount_plan["providers"]`` slot. The injection pattern mirrors openclaw's
``_inject_user_providers``: it happens AFTER
``prepared = await load_and_prepare_cached(...)`` returns and BEFORE
``engine.boot(params, bundle_override=prepared)`` is called — so the pickled
cache stays free of secrets (api_key is expanded per-invocation, not baked
into the pickle).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Catalog shape
# ---------------------------------------------------------------------------


def test_catalog_lists_all_four_detection_names() -> None:
    """PROVIDER_CATALOG keys exactly match KNOWN_PROVIDERS in provider_sources."""
    from amplifier_agent_cli.provider_sources import KNOWN_PROVIDERS, PROVIDER_CATALOG

    assert set(PROVIDER_CATALOG.keys()) == set(KNOWN_PROVIDERS)


def test_catalog_entry_shape() -> None:
    """Each catalog entry has module, source, env_var, legacy_env_vars, default_model."""
    from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG

    str_fields = {"module", "source", "env_var", "default_model"}
    required = str_fields | {"legacy_env_vars"}
    for name, entry in PROVIDER_CATALOG.items():
        missing = required - set(entry.keys())
        assert not missing, f"provider {name!r} missing fields {missing}"
        for key in str_fields:
            assert isinstance(entry[key], str), f"provider {name!r} field {key!r} must be str"
        # legacy_env_vars is a tuple of strings (possibly empty)
        legacy = entry["legacy_env_vars"]
        assert isinstance(legacy, tuple), f"provider {name!r} legacy_env_vars must be a tuple"
        for v in legacy:
            assert isinstance(v, str), f"provider {name!r} legacy_env_vars entry {v!r} must be str"
        # module name should match the canonical "provider-<short>" convention
        assert entry["module"].startswith("provider-")
        # source should be a git URI (everything mounts via amplifier-module-provider-X repos)
        assert entry["source"].startswith("git+https://"), f"{name!r} source must be git+https URI"


def test_catalog_anthropic_uses_anthropic_api_key() -> None:
    """The anthropic entry maps to ANTHROPIC_API_KEY for credential resolution."""
    from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG

    assert PROVIDER_CATALOG["anthropic"]["env_var"] == "ANTHROPIC_API_KEY"
    assert PROVIDER_CATALOG["openai"]["env_var"] == "OPENAI_API_KEY"
    # Azure uses the documented + upstream-preferred AZURE_OPENAI_API_KEY,
    # with AZURE_OPENAI_KEY accepted as a deprecated legacy alias.
    assert PROVIDER_CATALOG["azure-openai"]["env_var"] == "AZURE_OPENAI_API_KEY"
    assert PROVIDER_CATALOG["azure-openai"]["legacy_env_vars"] == ("AZURE_OPENAI_KEY",)
    assert PROVIDER_CATALOG["ollama"]["env_var"] == "OLLAMA_HOST"


# ---------------------------------------------------------------------------
# build_provider_entry
# ---------------------------------------------------------------------------


def test_build_provider_entry_for_anthropic_expands_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_provider_entry resolves the env var to its current value at call time."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-12345")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")

    assert entry["module"] == "provider-anthropic"
    assert entry["source"].startswith("git+https://")
    assert entry["config"]["api_key"] == "sk-ant-test-12345"
    assert entry["config"]["default_model"]
    assert entry["config"]["priority"] == 1


def test_build_provider_entry_for_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("openai")

    assert entry["module"] == "provider-openai"
    assert entry["config"]["api_key"] == "sk-openai-test"


def test_build_provider_entry_for_ollama_uses_host_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama uses OLLAMA_HOST (not an API key), but the field name stays 'api_key' for shape symmetry."""
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("ollama")

    assert entry["module"] == "provider-ollama"
    # The catalog stores the env value under api_key for consistent provider-module shape.
    assert entry["config"]["api_key"] == "http://localhost:11434"


def test_build_provider_entry_unknown_provider_raises() -> None:
    from amplifier_agent_cli.provider_sources import build_provider_entry

    with pytest.raises(ValueError) as excinfo:
        build_provider_entry("not-a-real-provider")
    assert "not-a-real-provider" in str(excinfo.value)


def test_build_provider_entry_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the env var is unset, api_key resolves to empty string (caller validated provider name earlier via config / bundle default)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")
    assert entry["config"]["api_key"] == ""


# ---------------------------------------------------------------------------
# inject_provider
# ---------------------------------------------------------------------------


def _stub_prepared(initial_providers: Any = None) -> Any:
    """Return a duck-typed PreparedBundle stand-in with a mount_plan dict."""
    mp: dict[str, Any] = {"session": {}, "tools": []}
    if initial_providers is not None:
        mp["providers"] = initial_providers
    return SimpleNamespace(mount_plan=mp)


def test_inject_provider_writes_single_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import inject_provider

    prepared = _stub_prepared()
    inject_provider(prepared, "anthropic")

    providers = prepared.mount_plan["providers"]
    assert isinstance(providers, list)
    assert len(providers) == 1
    assert providers[0]["module"] == "provider-anthropic"
    assert providers[0]["config"]["api_key"] == "sk-ant-test"


def test_inject_provider_does_not_clobber_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If mount_plan already has providers, inject_provider is a no-op (mirrors openclaw)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import inject_provider

    existing = [{"module": "provider-already-mounted", "source": "...", "config": {}}]
    prepared = _stub_prepared(initial_providers=existing)
    inject_provider(prepared, "anthropic")

    assert prepared.mount_plan["providers"] == existing  # unchanged


def test_inject_provider_unknown_name_raises() -> None:
    from amplifier_agent_cli.provider_sources import inject_provider

    prepared = _stub_prepared()
    with pytest.raises(ValueError):
        inject_provider(prepared, "not-a-real-provider")

def test_inject_provider_forwards_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """inject_provider forwards model_override and effort_override to build_provider_entry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import inject_provider

    prepared = _stub_prepared()
    inject_provider(prepared, "anthropic", model_override="claude-sonnet-4-5", effort_override="high")

    config = prepared.mount_plan["providers"][0]["config"]
    assert config["default_model"] == "claude-sonnet-4-5"
    assert config["effort"] == "high"


# ---------------------------------------------------------------------------
# build_provider_entry — model_override / effort_override
# ---------------------------------------------------------------------------


def test_build_provider_entry_model_override_replaces_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_override replaces the catalog default_model in the returned config."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic", model_override="claude-sonnet-4-5")

    assert entry["config"]["default_model"] == "claude-sonnet-4-5"


def test_build_provider_entry_no_model_override_keeps_catalog_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting model_override preserves the catalog's default_model value."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG, build_provider_entry

    entry = build_provider_entry("anthropic")

    assert entry["config"]["default_model"] == PROVIDER_CATALOG["anthropic"]["default_model"]


def test_build_provider_entry_effort_override_lands_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """effort_override is stored under config['effort'] when provided."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic", effort_override="high")

    assert entry["config"]["effort"] == "high"


def test_build_provider_entry_no_effort_override_omits_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """When effort_override is not passed, 'effort' must not appear in config at all."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")

    assert "effort" not in entry["config"]
