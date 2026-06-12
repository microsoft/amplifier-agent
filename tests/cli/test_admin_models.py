"""Tests for the models admin group (Task 7).

Verifies that `amplifier-agent models list`:
  - Is registered on the root CLI and reachable via --help (exit 0).
  - Exposes the --provider option in the help output.
"""

from __future__ import annotations

import json
import types

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from amplifier_agent_cli.admin import models as models_mod


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_models_list_is_registered(runner: CliRunner) -> None:
    """models list --help exits 0 and shows --provider option."""
    result = runner.invoke(cli, ["models", "list", "--help"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert "--provider" in result.output, f"Expected '--provider' in help output.\nOutput: {result.output}"


def test_get_provider_module_name_normalizes_prefix() -> None:
    """_get_provider_module_name strips 'provider-' prefix and normalizes dashes."""
    from amplifier_agent_cli.admin.models import _get_provider_module_name

    assert _get_provider_module_name("anthropic") == "amplifier_module_provider_anthropic"
    assert _get_provider_module_name("provider-anthropic") == "amplifier_module_provider_anthropic"
    assert _get_provider_module_name("azure-openai") == "amplifier_module_provider_azure_openai"


def test_load_provider_class_returns_none_for_unloadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_provider_class returns None (no raise) when _load_provider_module raises ImportError."""
    from amplifier_agent_cli.admin.models import load_provider_class

    def _raise_import_error(provider_id: str) -> None:
        raise ImportError("cannot load module")

    monkeypatch.setattr(models_mod, "_load_provider_module", _raise_import_error)
    result = load_provider_class("anthropic")
    assert result is None


def test_load_provider_class_finds_by_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_provider_class resolves {Name}Provider class by naming convention."""
    from amplifier_agent_cli.admin.models import load_provider_class

    class AnthropicProvider:
        pass

    fake_module = types.ModuleType("fake_anthropic_module")
    fake_module.AnthropicProvider = AnthropicProvider  # type: ignore[attr-defined]

    monkeypatch.setattr(models_mod, "_load_provider_module", lambda _: fake_module)
    result = load_provider_class("anthropic")
    assert result is AnthropicProvider


def test_try_instantiate_provider_standard_signature() -> None:
    """_try_instantiate_provider succeeds for a class with (api_key, config) signature."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    class StdProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            self.api_key = api_key
            self.config = config

    result = _try_instantiate_provider(StdProvider)
    assert isinstance(result, StdProvider)


def test_try_instantiate_provider_returns_none_when_all_fail() -> None:
    """_try_instantiate_provider returns None when all constructor signatures fail."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    class Unbuildable:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ValueError("always fails")

    result = _try_instantiate_provider(Unbuildable)
    assert result is None


def test_list_provider_models_calls_async_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_provider_models awaits async list_models() and calls close() in finally."""
    from amplifier_core import ModelInfo

    from amplifier_agent_cli.admin.models import list_provider_models

    closed = {"flag": False}

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def list_models(self) -> list[ModelInfo]:
            return [
                ModelInfo(
                    id="m1",
                    display_name="Model One",
                    context_window=1000,
                    max_output_tokens=100,
                )
            ]

        async def close(self) -> None:
            closed["flag"] = True

    monkeypatch.setattr(models_mod, "load_provider_class", lambda _: FakeProvider)
    models = list_provider_models("anthropic", timeout_seconds=5.0)
    assert [m.id for m in models] == ["m1"]
    assert closed["flag"] is True


def test_list_provider_models_propagates_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_provider_models lets list_models() exceptions propagate (no swallowing)."""
    from amplifier_agent_cli.admin.models import list_provider_models

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def list_models(self) -> None:
            raise RuntimeError("missing ANTHROPIC_API_KEY")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(models_mod, "load_provider_class", lambda _: FakeProvider)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        list_provider_models("anthropic", timeout_seconds=5.0)


def test_models_list_json_envelope_shape(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list --output json emits a JSON envelope with the expected schema."""
    from amplifier_core import ModelInfo

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="claude-sonnet-4-5",
                display_name="Claude Sonnet 4.5",
                context_window=200000,
                max_output_tokens=8192,
                capabilities=["tools", "vision", "thinking"],
            )
        ]

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1, payload
    assert payload["provider"] == "anthropic", payload
    assert "fetched_at" in payload, payload
    assert payload["models"][0]["id"] == "claude-sonnet-4-5", payload
    assert payload["models"][0]["capabilities"] == ["tools", "vision", "thinking"], payload


def test_models_list_table_columns(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list --output table renders 4 columns with correct headers and values."""
    from amplifier_core import ModelInfo

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="claude-sonnet-4-5",
                display_name="Claude Sonnet 4.5",
                context_window=200000,
                max_output_tokens=8192,
                capabilities=["tools", "vision", "thinking"],
            )
        ]

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "table"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    # Headers must be present
    assert "ID" in result.output, f"Expected 'ID' in output:\n{result.output}"
    assert "DISPLAY NAME" in result.output, f"Expected 'DISPLAY NAME' in output:\n{result.output}"
    assert "CONTEXT" in result.output, f"Expected 'CONTEXT' in output:\n{result.output}"
    assert "CAPABILITIES" in result.output, f"Expected 'CAPABILITIES' in output:\n{result.output}"
    # Data values must be present
    assert "claude-sonnet-4-5" in result.output, f"Expected 'claude-sonnet-4-5' in output:\n{result.output}"
    assert "Claude Sonnet 4.5" in result.output, f"Expected 'Claude Sonnet 4.5' in output:\n{result.output}"
    assert "200000" in result.output, f"Expected '200000' in output:\n{result.output}"
    assert "tools, vision, thinking" in result.output, f"Expected 'tools, vision, thinking' in output:\n{result.output}"


def test_models_list_provider_error_exits_2(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list exits 2 with stderr message when list_provider_models raises."""

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> None:
        raise RuntimeError("missing ANTHROPIC_API_KEY")

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}. Output:\n{result.output}"
    assert "ANTHROPIC_API_KEY" in result.stderr, f"Expected 'ANTHROPIC_API_KEY' in stderr.\nStderr: {result.stderr}"
    assert result.stdout.strip() == "", f"Expected empty stdout.\nStdout: {result.stdout}"


def test_models_list_empty_exits_0_with_advisory(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list exits 0 with advisory on stderr when provider returns empty model list."""

    monkeypatch.setattr(models_mod, "list_provider_models", lambda *a, **kw: [])
    result = runner.invoke(cli, ["models", "list", "--provider", "azure-openai", "--output", "json"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert "azure-openai" in result.stderr, f"Expected 'azure-openai' in stderr.\nStderr: {result.stderr}"
    assert "no live model list" in result.stderr, f"Expected 'no live model list' in stderr.\nStderr: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["models"] == [], f"Expected empty models list.\nPayload: {payload}"


def test_models_list_unknown_provider_exits_1(runner: CliRunner) -> None:
    """models list exits 1 with provider name in stderr for an unknown provider.

    click.ClickException (raised by the PROVIDER_CATALOG guard in Task 12)
    writes 'Error: ...' to stderr and exits with code 1, not 2.  Code 2 is
    reserved for runtime errors from the provider/live-call path.
    """
    result = runner.invoke(cli, ["models", "list", "--provider", "not-a-provider"])
    assert result.exit_code == 1, f"Expected exit 1, got {result.exit_code}. Output:\n{result.output}"
    assert "not-a-provider" in result.stderr, f"Expected 'not-a-provider' in stderr.\nStderr: {result.stderr}"


# ---------------------------------------------------------------------------
# Regression tests for env-var credential resolution and module-not-installed
# distinguishability (DTU integration testing found `models list` was passing
# api_key="" to provider constructors instead of reading from env, and was
# silently returning [] when the provider module wasn't pip-installed yet).
# ---------------------------------------------------------------------------


def test_resolve_provider_credentials_anthropic_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials reads ANTHROPIC_API_KEY from env for anthropic."""
    from amplifier_agent_cli.admin.models import _resolve_provider_credentials

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-anthropic-real")
    creds = _resolve_provider_credentials("anthropic")
    assert creds.get("api_key") == "ak-anthropic-real"


def test_resolve_provider_credentials_openai_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials reads OPENAI_API_KEY from env for openai."""
    from amplifier_agent_cli.admin.models import _resolve_provider_credentials

    monkeypatch.setenv("OPENAI_API_KEY", "ak-openai-real")
    creds = _resolve_provider_credentials("openai")
    assert creds.get("api_key") == "ak-openai-real"


def test_resolve_provider_credentials_azure_openai_reads_preferred_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials reads AZURE_OPENAI_API_KEY for azure-openai."""
    from amplifier_agent_cli.admin.models import _resolve_provider_credentials

    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "ak-azure-preferred")
    monkeypatch.delenv("AZURE_OPENAI_KEY", raising=False)
    creds = _resolve_provider_credentials("azure-openai")
    assert creds.get("api_key") == "ak-azure-preferred"


def test_resolve_provider_credentials_azure_openai_legacy_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials falls back to AZURE_OPENAI_KEY when preferred is unset."""
    from amplifier_agent_cli.admin.models import _resolve_provider_credentials

    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_KEY", "ak-azure-legacy")
    creds = _resolve_provider_credentials("azure-openai")
    assert creds.get("api_key") == "ak-azure-legacy"


def test_resolve_provider_credentials_ollama_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials returns localhost default for ollama when no env set."""
    from amplifier_agent_cli.admin.models import _resolve_provider_credentials

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    creds = _resolve_provider_credentials("ollama")
    assert creds.get("host") == "http://localhost:11434"


def test_resolve_provider_credentials_ollama_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials reads OLLAMA_HOST when set."""
    from amplifier_agent_cli.admin.models import _resolve_provider_credentials

    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.example.com:11434")
    creds = _resolve_provider_credentials("ollama")
    assert creds.get("host") == "http://ollama.example.com:11434"


def test_resolve_provider_credentials_anthropic_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials raises ProviderCredentialsMissingError when key absent."""
    from amplifier_agent_cli.admin.models import (
        ProviderCredentialsMissingError,
        _resolve_provider_credentials,
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderCredentialsMissingError, match="ANTHROPIC_API_KEY"):
        _resolve_provider_credentials("anthropic")


def test_resolve_provider_credentials_openai_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials raises when OPENAI_API_KEY absent."""
    from amplifier_agent_cli.admin.models import (
        ProviderCredentialsMissingError,
        _resolve_provider_credentials,
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderCredentialsMissingError, match="OPENAI_API_KEY"):
        _resolve_provider_credentials("openai")


def test_resolve_provider_credentials_azure_openai_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_provider_credentials raises when both AZURE_OPENAI_API_KEY and legacy absent."""
    from amplifier_agent_cli.admin.models import (
        ProviderCredentialsMissingError,
        _resolve_provider_credentials,
    )

    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_KEY", raising=False)
    with pytest.raises(ProviderCredentialsMissingError, match="AZURE_OPENAI_API_KEY"):
        _resolve_provider_credentials("azure-openai")


def test_try_instantiate_provider_uses_credentials_api_key() -> None:
    """_try_instantiate_provider passes credentials['api_key'] to the constructor."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["api_key"] = api_key
            captured["config"] = config

    result = _try_instantiate_provider(CapturingProvider, credentials={"api_key": "real-key"})
    assert isinstance(result, CapturingProvider)
    assert captured["api_key"] == "real-key", (
        f"Expected api_key='real-key' to reach the constructor, got {captured['api_key']!r}. "
        "This means the credentials dict is being ignored and the placeholder is winning."
    )


def test_try_instantiate_provider_uses_credentials_host() -> None:
    """_try_instantiate_provider passes credentials['host'] to ollama-style constructor."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    captured: dict[str, object] = {}

    class OllamaStyleProvider:
        def __init__(self, host: str, config: dict) -> None:
            captured["host"] = host
            captured["config"] = config

    result = _try_instantiate_provider(OllamaStyleProvider, credentials={"host": "http://ollama.example.com:11434"})
    assert isinstance(result, OllamaStyleProvider)
    assert captured["host"] == "http://ollama.example.com:11434"


def test_try_instantiate_provider_backward_compat_no_credentials() -> None:
    """_try_instantiate_provider still works when called with no credentials (default empty)."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    class StdProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            self.api_key = api_key
            self.config = config

    result = _try_instantiate_provider(StdProvider)
    assert isinstance(result, StdProvider)
    # When no credentials passed, falls back to empty string (preserves prior behaviour).
    assert result.api_key == ""


def test_list_provider_models_passes_env_api_key_to_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_provider_models reads ANTHROPIC_API_KEY and passes it to the provider constructor.

    This is the primary regression test for the DTU-discovered bug: previously,
    api_key="" was hardcoded, so the Anthropic SDK rejected the explicit empty
    string instead of falling back to the env var.
    """
    from amplifier_agent_cli.admin.models import list_provider_models

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["api_key"] = api_key
            captured["config"] = config

        async def list_models(self) -> list[object]:
            return []

        async def close(self) -> None:
            return None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-from-env-12345")
    monkeypatch.setattr(models_mod, "load_provider_class", lambda _: CapturingProvider)
    list_provider_models("anthropic", timeout_seconds=5.0)
    assert captured["api_key"] == "ak-from-env-12345", (
        f"Expected env-var ANTHROPIC_API_KEY to reach the provider constructor, "
        f"got {captured['api_key']!r}. Bug: api_key='' hardcoded in _try_instantiate_provider."
    )


def test_list_provider_models_raises_credentials_missing_for_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_provider_models raises ProviderCredentialsMissingError when ANTHROPIC_API_KEY absent."""
    from amplifier_agent_cli.admin.models import (
        ProviderCredentialsMissingError,
        list_provider_models,
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderCredentialsMissingError, match="ANTHROPIC_API_KEY"):
        list_provider_models("anthropic", timeout_seconds=5.0)


def test_list_provider_models_raises_module_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_provider_models raises ProviderModuleNotInstalledError when module import fails.

    Previously, ImportError was caught silently by load_provider_class and the
    CLI rendered an empty list with a misleading "no live model list available"
    advisory.  The fix surfaces the install gap distinctly.
    """
    from amplifier_agent_cli.admin.models import (
        ProviderModuleNotInstalledError,
        list_provider_models,
    )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-real")

    def _raise_import(provider_id: str) -> None:
        raise ImportError(f"No module named 'amplifier_module_provider_{provider_id}'")

    monkeypatch.setattr(models_mod, "_load_provider_module", _raise_import)
    with pytest.raises(ProviderModuleNotInstalledError, match="anthropic"):
        list_provider_models("anthropic", timeout_seconds=5.0)


def test_models_list_credentials_missing_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """models list exits 2 with stderr explaining the missing env var.

    User-visible contract: exit 2 (not 0 + misleading empty list).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}. Output:\n{result.output}"
    assert "ANTHROPIC_API_KEY" in result.stderr, (
        f"Expected 'ANTHROPIC_API_KEY' in stderr for actionable user guidance.\nStderr: {result.stderr}"
    )
    assert result.stdout.strip() == "", f"Expected empty stdout on error.\nStdout: {result.stdout}"


def test_models_list_module_not_installed_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """models list exits 2 with an install hint when the provider module isn't pip-installed.

    Previously returned exit 0 with empty models + 'no live model list available' advisory,
    which was visually identical to the legitimate azure-openai empty case. Now distinct.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-real")

    def _raise_import(provider_id: str) -> None:
        raise ImportError(f"No module named 'amplifier_module_provider_{provider_id}'")

    monkeypatch.setattr(models_mod, "_load_provider_module", _raise_import)
    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}. Output:\n{result.output}"
    # Stderr must give the user an actionable install hint (NOT the misleading
    # "no live model list available" advisory the bug used to emit).
    msg = result.stderr.lower()
    assert "not installed" in msg or "not pip-installed" in msg, (
        f"Expected 'not installed' guidance in stderr.\nStderr: {result.stderr}"
    )
    assert "no live model list available" not in result.stderr, (
        f"Stderr leaked the misleading legacy advisory message.\nStderr: {result.stderr}"
    )
    assert result.stdout.strip() == "", f"Expected empty stdout on error.\nStdout: {result.stdout}"


# ---------------------------------------------------------------------------
# Cycle 2: filter flip — default unfiltered, --latest opt-in
#
# Anthropic's list_models() defaults to filtered=True, collapsing every
# response to one model per family (opus / sonnet / haiku → 3 total).
# DTU integration testing surfaced that users running `models list
# --provider anthropic` were confused by seeing only 3 entries when the
# API returns many more.
#
# Fix: flip the CLI's discovery-time default to filtered=False so users
# see the full list. Add a --latest flag for the previous behavior.
# Provider-module default stays filtered=True (other callers — spawn_utils,
# routing-matrix resolver — depend on that).
# ---------------------------------------------------------------------------


def test_models_list_default_passes_filtered_false_to_provider(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list (no --latest) constructs the provider with config={"filtered": False}.

    The CLI's discovery-time default differs from the provider module's own
    list_models() default: when a user runs `models list`, they want every
    model. Asserts the constructor receives the explicit filter override.
    """
    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["api_key"] = api_key
            captured["config"] = config

        async def list_models(self) -> list[object]:
            return []

        async def close(self) -> None:
            return None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-real")
    monkeypatch.setattr(models_mod, "load_provider_class", lambda _: CapturingProvider)

    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    # Empty list path → exit 0 + stderr advisory. We only care about the constructor args here.
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert captured["config"] == {"filtered": False}, (
        f"Expected provider config to carry filtered=False by default; got {captured['config']!r}. "
        "Discovery-time default should show every model unless --latest is passed."
    )


def test_models_list_latest_flag_passes_filtered_true(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list --latest constructs the provider with config={"filtered": True}.

    The provider module's list_models() then collapses to one model per
    family (latest-per-family filter) — restoring the pre-flip behavior.
    """
    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["config"] = config

        async def list_models(self) -> list[object]:
            return []

        async def close(self) -> None:
            return None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-real")
    monkeypatch.setattr(models_mod, "load_provider_class", lambda _: CapturingProvider)

    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--latest", "--output", "json"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert captured["config"] == {"filtered": True}, (
        f"Expected provider config to carry filtered=True with --latest; got {captured['config']!r}."
    )


def test_models_list_help_mentions_latest_flag(runner: CliRunner) -> None:
    """models list --help advertises --latest."""
    result = runner.invoke(cli, ["models", "list", "--help"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert "--latest" in result.output, f"Expected '--latest' in help output:\n{result.output}"


def test_list_provider_models_forwards_extra_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_provider_models forwards extra_config to the provider constructor."""
    from amplifier_agent_cli.admin.models import list_provider_models

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["config"] = config

        async def list_models(self) -> list[object]:
            return []

        async def close(self) -> None:
            return None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-real")
    monkeypatch.setattr(models_mod, "load_provider_class", lambda _: CapturingProvider)

    list_provider_models("anthropic", timeout_seconds=5.0, extra_config={"filtered": True})
    assert captured["config"] == {"filtered": True}, (
        f"Expected extra_config={{'filtered': True}} forwarded to constructor; got {captured['config']!r}."
    )


def test_try_instantiate_provider_accepts_extra_config() -> None:
    """_try_instantiate_provider forwards extra_config to the constructor's config arg."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["config"] = config

    result = _try_instantiate_provider(
        CapturingProvider, credentials={"api_key": "k"}, extra_config={"filtered": False}
    )
    assert isinstance(result, CapturingProvider)
    assert captured["config"] == {"filtered": False}, (
        f"Expected extra_config to land in constructor's config; got {captured['config']!r}."
    )


def test_try_instantiate_provider_extra_config_defaults_to_empty() -> None:
    """_try_instantiate_provider preserves the empty-config default when extra_config is None."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            captured["config"] = config

    _try_instantiate_provider(CapturingProvider, credentials={"api_key": "k"})
    assert captured["config"] == {}, f"Expected empty config when extra_config not passed; got {captured['config']!r}."


# ---------------------------------------------------------------------------
# Cycle 3: aggregate mode — `models list` without --provider iterates
# every provider in parallel and returns a per-provider status envelope.
#
# Exit codes:
#   0  → at least one provider returned status == "ok" (empty model list is OK)
#   2  → ALL providers failed (no successful enumeration)
#
# Per-provider statuses:
#   ok                      → list_models returned (possibly empty)
#   credentials_missing     → ProviderCredentialsMissingError raised
#   module_not_installed    → ProviderModuleNotInstalledError raised
#   error                   → any other exception (message captured)
# ---------------------------------------------------------------------------


def test_models_list_no_provider_is_allowed(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """models list without --provider runs aggregate mode (no required-option error)."""

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[object]:
        return []

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "json"])
    # Aggregate mode with all-empty providers should not raise UsageError.
    assert "Missing option" not in result.output, (
        f"--provider should be optional in aggregate mode.\nOutput: {result.output}"
    )


def test_models_list_aggregate_json_envelope_shape(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list aggregate JSON envelope contains a per-provider results array."""
    from amplifier_core import ModelInfo

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=f"{provider_id}-model-1",
                display_name=f"{provider_id} Model 1",
                context_window=100000,
                max_output_tokens=4096,
            )
        ]

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "json"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    payload = json.loads(result.output)

    assert payload.get("schema_version") == 1, payload
    assert "fetched_at" in payload, payload
    assert isinstance(payload.get("results"), list), payload
    # Aggregate envelope must NOT carry a top-level provider field (single-provider only).
    assert "provider" not in payload, payload

    providers_seen = {entry["provider"] for entry in payload["results"]}
    from amplifier_agent_cli.provider_sources import KNOWN_PROVIDERS

    assert providers_seen == set(KNOWN_PROVIDERS), (
        f"Expected results entry per known provider; got {sorted(providers_seen)}"
    )
    for entry in payload["results"]:
        assert entry["status"] == "ok", entry
        assert entry["models"], f"provider {entry['provider']} should have models populated"


def test_models_list_aggregate_credentials_missing_records_status(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate mode: a provider raising ProviderCredentialsMissingError gets credentials_missing."""

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[object]:
        if provider_id == "openai":
            from amplifier_agent_cli.admin.models import ProviderCredentialsMissingError

            raise ProviderCredentialsMissingError("OPENAI_API_KEY not set")
        return []

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "json"])

    # At least one provider succeeded → exit 0.
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    payload = json.loads(result.output)
    by_provider = {entry["provider"]: entry for entry in payload["results"]}

    assert by_provider["openai"]["status"] == "credentials_missing", by_provider["openai"]
    assert by_provider["openai"]["models"] == [], by_provider["openai"]
    assert "OPENAI_API_KEY" in by_provider["openai"].get("error", ""), by_provider["openai"]
    # Other providers untouched
    assert by_provider["anthropic"]["status"] == "ok", by_provider["anthropic"]


def test_models_list_aggregate_module_not_installed_records_status(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate mode: provider raising ProviderModuleNotInstalledError gets module_not_installed."""

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[object]:
        if provider_id == "ollama":
            from amplifier_agent_cli.admin.models import ProviderModuleNotInstalledError

            raise ProviderModuleNotInstalledError("provider module not installed for 'ollama'")
        return []

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "json"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    payload = json.loads(result.output)
    by_provider = {entry["provider"]: entry for entry in payload["results"]}

    assert by_provider["ollama"]["status"] == "module_not_installed", by_provider["ollama"]
    assert by_provider["ollama"]["models"] == [], by_provider["ollama"]
    assert "ollama" in by_provider["ollama"].get("error", "").lower(), by_provider["ollama"]


def test_models_list_aggregate_generic_error_records_status(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate mode: any other exception is recorded as status='error' with message."""

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[object]:
        if provider_id == "anthropic":
            raise RuntimeError("network blew up")
        return []

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "json"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    payload = json.loads(result.output)
    by_provider = {entry["provider"]: entry for entry in payload["results"]}

    assert by_provider["anthropic"]["status"] == "error", by_provider["anthropic"]
    assert "network blew up" in by_provider["anthropic"].get("error", ""), by_provider["anthropic"]


def test_models_list_aggregate_all_failed_exits_2(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate mode exits 2 when no provider managed to enumerate."""
    from amplifier_agent_cli.admin.models import ProviderCredentialsMissingError

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[object]:
        raise ProviderCredentialsMissingError(f"{provider_id.upper()}_API_KEY not set")

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "json"])

    assert result.exit_code == 2, f"Expected exit 2 when every provider fails. Output:\n{result.output}"
    # The aggregate envelope is still emitted to stdout so the caller can
    # inspect the per-provider error breakdown.
    payload = json.loads(result.output)
    assert all(entry["status"] != "ok" for entry in payload["results"]), payload


def test_models_list_aggregate_table_includes_status_column(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate table output shows PROVIDER + STATUS + MODELS columns and a status per row."""
    from amplifier_core import ModelInfo

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **_: object) -> list[ModelInfo]:
        if provider_id == "openai":
            from amplifier_agent_cli.admin.models import ProviderCredentialsMissingError

            raise ProviderCredentialsMissingError("OPENAI_API_KEY not set")
        if provider_id == "anthropic":
            return [
                ModelInfo(
                    id="claude-x",
                    display_name="Claude X",
                    context_window=100000,
                    max_output_tokens=4096,
                )
            ]
        return []

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(cli, ["models", "list", "--output", "table"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    out = result.output
    assert "PROVIDER" in out, out
    assert "STATUS" in out, out
    assert "MODELS" in out, out
    assert "ok" in out, out
    assert "credentials_missing" in out, out
    assert "anthropic" in out, out
    assert "openai" in out, out


def test_models_list_aggregate_passes_extra_config(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate mode forwards the filter directive to every provider call.

    `--latest` in aggregate mode means \"latest per family across all providers\"; the
    default mode means \"every model across all providers\".
    """
    seen_configs: list[object] = []

    def fake_list(provider_id: str, timeout_seconds: float = 15.0, **kwargs: object) -> list[object]:
        seen_configs.append(kwargs.get("extra_config"))
        return []

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    runner.invoke(cli, ["models", "list", "--output", "json"])
    assert seen_configs, "fake_list never called"
    assert all(c == {"filtered": False} for c in seen_configs), (
        f"Expected every provider to receive filtered=False; got {seen_configs}"
    )

    seen_configs.clear()
    runner.invoke(cli, ["models", "list", "--latest", "--output", "json"])
    assert all(c == {"filtered": True} for c in seen_configs), (
        f"Expected every provider to receive filtered=True with --latest; got {seen_configs}"
    )
