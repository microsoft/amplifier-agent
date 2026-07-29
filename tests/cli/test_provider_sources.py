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


def test_catalog_entry_shape_is_bootstrap_only() -> None:
    """Catalog entries are bootstrap-only: module + source, nothing else.

    Mirrors ``amplifier_app_cli.DEFAULT_PROVIDER_SOURCES`` — the catalog tells
    the kernel WHERE to install a provider from. Everything else (env vars,
    default model, credential fields) flows from ``provider.get_info()`` at
    runtime, so the catalog can never drift from provider truth.

    Pre-existing fields removed in the shrink: ``env_var``,
    ``legacy_env_vars``, ``default_model``. The env-var mapping moved to
    ``PROVIDER_CREDENTIAL_VARS`` (a separate small auxiliary mapping); the
    default model is sourced from ``provider.get_info().defaults["model"]``.
    """
    from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG

    required = {"module", "source"}
    removed = {"env_var", "legacy_env_vars", "default_model"}
    for name, entry in PROVIDER_CATALOG.items():
        keys = set(entry.keys())
        missing = required - keys
        assert not missing, f"provider {name!r} missing required bootstrap fields {missing}"
        leaked = keys & removed
        assert not leaked, (
            f"provider {name!r} still has shrink-removed catalog fields {leaked}; "
            f"these belong in PROVIDER_CREDENTIAL_VARS / provider.get_info() instead."
        )
        for key in required:
            assert isinstance(entry[key], str), f"provider {name!r} field {key!r} must be str"
        # module name should match the canonical "provider-<short>" convention
        assert entry["module"].startswith("provider-")
        # source should be a git URI (everything mounts via amplifier-module-provider-X repos)
        assert entry["source"].startswith("git+https://"), f"{name!r} source must be git+https URI"


def test_credential_vars_mapping_matches_known_providers() -> None:
    """PROVIDER_CREDENTIAL_VARS covers every known provider with (primary, *legacy)."""
    from amplifier_agent_cli.provider_sources import KNOWN_PROVIDERS, PROVIDER_CREDENTIAL_VARS

    assert set(PROVIDER_CREDENTIAL_VARS.keys()) == set(KNOWN_PROVIDERS)
    for name, env_vars in PROVIDER_CREDENTIAL_VARS.items():
        assert isinstance(env_vars, tuple), f"{name!r} credential vars must be a tuple"
        assert env_vars, f"{name!r} credential vars must be non-empty"
        for v in env_vars:
            assert isinstance(v, str) and v, f"{name!r} env var entry {v!r} must be non-empty str"

    # Primary env var (entry[0]) is the documented preferred name.
    assert PROVIDER_CREDENTIAL_VARS["anthropic"][0] == "ANTHROPIC_API_KEY"
    assert PROVIDER_CREDENTIAL_VARS["openai"][0] == "OPENAI_API_KEY"
    assert PROVIDER_CREDENTIAL_VARS["azure-openai"][0] == "AZURE_OPENAI_API_KEY"
    # Azure carries a legacy alias (AZURE_OPENAI_KEY) for backwards compat.
    assert "AZURE_OPENAI_KEY" in PROVIDER_CREDENTIAL_VARS["azure-openai"][1:]
    assert PROVIDER_CREDENTIAL_VARS["ollama"][0] == "OLLAMA_HOST"


# ---------------------------------------------------------------------------
# build_provider_entry
# ---------------------------------------------------------------------------


def test_build_provider_entry_for_anthropic_expands_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_provider_entry resolves the env var to its current value at call time.

    With no model_override, default_model is OMITTED from config so the
    kernel/provider sources the default from provider.get_info() rather than
    a stale catalog value. Mirrors amplifier_app_cli.configure_provider's
    "no hard-coded provider defaults" comment.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-12345")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")

    assert entry["module"] == "provider-anthropic"
    assert entry["source"].startswith("git+https://")
    assert entry["config"]["api_key"] == "sk-ant-test-12345"
    assert entry["config"]["priority"] == 1
    # default_model is OMITTED unless an override is given — catalog no longer
    # carries a default_model field that could drift from provider truth.
    assert "default_model" not in entry["config"], (
        f"Expected default_model to be omitted from config without an override; "
        f"got {entry['config'].get('default_model')!r}. Provider self-describes its default via get_info()."
    )


def test_build_provider_entry_for_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("openai")

    assert entry["module"] == "provider-openai"
    assert entry["config"]["api_key"] == "sk-openai-test"


def test_build_provider_entry_for_ollama_uses_host_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama uses OLLAMA_HOST, surfaced under config['host'] (not 'api_key').

    Verified against amplifier-module-provider-ollama's own config reader
    (``config.get("host")``) -- see ``test_ollama_run_mount_uses_host`` in
    ``test_credential_resolution.py`` for the dedicated regression test.
    """
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("ollama")

    assert entry["module"] == "provider-ollama"
    assert entry["config"]["host"] == "http://localhost:11434"
    assert "api_key" not in entry["config"]


def test_build_provider_entry_unknown_provider_raises() -> None:
    from amplifier_agent_cli.provider_sources import build_provider_entry

    with pytest.raises(ValueError) as excinfo:
        build_provider_entry("not-a-real-provider")
    assert "not-a-real-provider" in str(excinfo.value)


def test_build_provider_entry_missing_env_var(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If nothing resolves, ``config`` has no api_key key (caller validated provider name
    earlier via config / bundle default; downstream ``_try_instantiate_provider`` defaults
    missing keys to "" itself, so omission -- not an empty-string placeholder -- is the
    contract; see CredentialResolution.fields == {} for the unresolved case)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Isolate the real credentials store too: without this, a developer's own
    # ``~/.amplifier-agent/credentials.json`` (from prior `auth set` usage)
    # leaks in via the env->file fallback and this assertion fails spuriously.
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")
    assert "api_key" not in entry["config"]
    assert entry["config"].get("api_key", "") == ""


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


def test_build_provider_entry_no_model_override_omits_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting model_override leaves default_model out of config entirely.

    Catalog shrink contract: build_provider_entry never injects a
    catalog-side default_model. When the caller doesn't supply one, the
    kernel mounts the provider with whatever the provider's own
    get_info().defaults["model"] returns. This is exactly amplifier_app_cli's
    "no hard-coded provider defaults" rule.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")

    assert "default_model" not in entry["config"], (
        f"Expected default_model to be absent when no override given; got {entry['config'].get('default_model')!r}"
    )


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


# ---------------------------------------------------------------------------
# extra_config -- forward-compat pass-through dict
#
# host_config.provider.config is the single source of truth for provider
# configuration. The engine forwards the entire dict (minus the credentials it
# resolves itself) into build_provider_entry via the ``extra_config`` kwarg,
# which is overlaid onto the final config. This lets new provider-side knobs
# (temperature, max_tokens, thinking_budget_tokens, ...) thread through
# without requiring an engine release.
# ---------------------------------------------------------------------------


def test_build_provider_entry_extra_config_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arbitrary keys in extra_config land verbatim in the returned config."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry(
        "anthropic",
        extra_config={"temperature": 0.3, "max_tokens": 4096, "thinking_budget_tokens": 8192},
    )

    assert entry["config"]["temperature"] == 0.3
    assert entry["config"]["max_tokens"] == 4096
    assert entry["config"]["thinking_budget_tokens"] == 8192


def test_build_provider_entry_extra_config_includes_default_model_and_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra_config can carry default_model and effort; both land in config.

    The engine forwards the entire host_config["provider"]["config"] dict
    through extra_config, so these well-known keys are not special-cased --
    they pass through alongside the arbitrary ones.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry(
        "anthropic",
        extra_config={"default_model": "claude-sonnet-4-5", "effort": "high"},
    )

    assert entry["config"]["default_model"] == "claude-sonnet-4-5"
    assert entry["config"]["effort"] == "high"


def test_build_provider_entry_extra_config_does_not_clobber_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra_config must not override the env-resolved api_key.

    Credentials are resolved per-invocation from env vars (so the pickle cache
    never holds secrets). A host_config that accidentally includes ``api_key``
    in ``provider.config`` must not be allowed to overwrite the env-derived
    value -- doing so would let a stale config file silently downgrade the
    credential.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic", extra_config={"api_key": "stale-value"})

    assert entry["config"]["api_key"] == "sk-ant-real"


def test_build_provider_entry_extra_config_does_not_clobber_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra_config must not override the runtime-asserted priority.

    ``priority`` is set by the engine (currently always ``1`` -- there is only
    ever one provider mounted in this CLI). A host_config that accidentally
    includes ``priority`` in ``provider.config`` must not be allowed to
    overwrite the engine-asserted value -- the field belongs to the mount
    machinery, not to user-tunable provider knobs.

    Pairs with ``test_build_provider_entry_extra_config_does_not_clobber_api_key``
    -- both fields belong to the same "engine-asserted, not user-tunable" class.
    Protecting them both via the same mechanism keeps the convention explicit
    and uniform (and makes it obvious where to add the next protected key).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic", extra_config={"priority": 999})

    assert entry["config"]["priority"] == 1


def test_build_provider_entry_no_extra_config_omits_pass_through_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No extra_config -> config has only api_key + priority (nothing extra)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")

    assert set(entry["config"].keys()) == {"api_key", "priority"}


def test_inject_provider_forwards_extra_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """inject_provider forwards extra_config to build_provider_entry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import inject_provider

    prepared = _stub_prepared()
    inject_provider(
        prepared,
        "anthropic",
        extra_config={"default_model": "claude-sonnet-4-5", "temperature": 0.5},
    )

    cfg = prepared.mount_plan["providers"][0]["config"]
    assert cfg["default_model"] == "claude-sonnet-4-5"
    assert cfg["temperature"] == 0.5


# ---------------------------------------------------------------------------
# Reseller model-id namespacing
# ---------------------------------------------------------------------------


def test_namespace_model_id_prefixes_reseller() -> None:
    from amplifier_agent_cli.provider_sources import namespace_model_id

    assert namespace_model_id("github-copilot", "claude-sonnet-5") == "github-copilot/claude-sonnet-5"


def test_namespace_model_id_leaves_native_providers_bare() -> None:
    """Native ids must stay bare -- namespacing them would break existing clients."""
    from amplifier_agent_cli.provider_sources import namespace_model_id

    assert namespace_model_id("anthropic", "claude-sonnet-5") == "claude-sonnet-5"
    assert namespace_model_id(None, "claude-sonnet-5") == "claude-sonnet-5"


def test_namespace_model_id_is_idempotent() -> None:
    from amplifier_agent_cli.provider_sources import namespace_model_id

    once = namespace_model_id("github-copilot", "claude-sonnet-5")
    assert namespace_model_id("github-copilot", once) == once


def test_split_model_id_round_trips_reseller() -> None:
    from amplifier_agent_cli.provider_sources import namespace_model_id, split_model_id

    qualified = namespace_model_id("github-copilot", "claude-sonnet-5")
    assert split_model_id(qualified) == ("github-copilot", "claude-sonnet-5")


def test_split_model_id_leaves_native_untouched() -> None:
    from amplifier_agent_cli.provider_sources import split_model_id

    assert split_model_id("claude-sonnet-5") == (None, "claude-sonnet-5")


def test_split_model_id_preserves_non_reseller_separator() -> None:
    """An id that merely contains a separator is not a namespace.

    Some upstreams ship ids like ``vendor/model``. Only a recognised reseller
    prefix may be stripped, otherwise the bare id handed to the provider would be
    silently truncated.
    """
    from amplifier_agent_cli.provider_sources import split_model_id

    assert split_model_id("openai/gpt-4o") == (None, "openai/gpt-4o")


def test_reseller_namespacing_and_display_suffix_share_one_source() -> None:
    """Both behaviors key off RESELLER_PROVIDERS, so they cannot disagree."""
    from amplifier_agent_cli.provider_sources import (
        RESELLER_PROVIDERS,
        decorate_display_name,
        namespace_model_id,
    )

    for provider in RESELLER_PROVIDERS:
        assert namespace_model_id(provider, "m") != "m", f"{provider} not namespaced"
        assert decorate_display_name(provider, "M") != "M", f"{provider} not suffixed"
