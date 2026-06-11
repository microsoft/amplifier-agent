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
    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    )
    assert "--provider" in result.output, (
        f"Expected '--provider' in help output.\nOutput: {result.output}"
    )


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

    def fake_list(
        provider_id: str, timeout_seconds: float = 15.0
    ) -> list[ModelInfo]:
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
    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    )
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

    def fake_list(
        provider_id: str, timeout_seconds: float = 15.0
    ) -> list[ModelInfo]:
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
    result = runner.invoke(
        cli, ["models", "list", "--provider", "anthropic", "--output", "table"]
    )
    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    )
    # Headers must be present
    assert "ID" in result.output, f"Expected 'ID' in output:\n{result.output}"
    assert "DISPLAY NAME" in result.output, (
        f"Expected 'DISPLAY NAME' in output:\n{result.output}"
    )
    assert "CONTEXT" in result.output, (
        f"Expected 'CONTEXT' in output:\n{result.output}"
    )
    assert "CAPABILITIES" in result.output, (
        f"Expected 'CAPABILITIES' in output:\n{result.output}"
    )
    # Data values must be present
    assert "claude-sonnet-4-5" in result.output, (
        f"Expected 'claude-sonnet-4-5' in output:\n{result.output}"
    )
    assert "Claude Sonnet 4.5" in result.output, (
        f"Expected 'Claude Sonnet 4.5' in output:\n{result.output}"
    )
    assert "200000" in result.output, (
        f"Expected '200000' in output:\n{result.output}"
    )
    assert "tools, vision, thinking" in result.output, (
        f"Expected 'tools, vision, thinking' in output:\n{result.output}"
    )


def test_models_list_provider_error_exits_2(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list exits 2 with stderr message when list_provider_models raises."""

    def fake_list(provider_id: str, timeout_seconds: float = 15.0) -> None:
        raise RuntimeError("missing ANTHROPIC_API_KEY")

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)
    result = runner.invoke(
        cli, ["models", "list", "--provider", "anthropic", "--output", "json"]
    )
    assert result.exit_code == 2, (
        "Expected exit 2, got {}. Output:\n{}".format(result.exit_code, result.output)
    )
    assert "ANTHROPIC_API_KEY" in result.stderr, (
        "Expected 'ANTHROPIC_API_KEY' in stderr.\nStderr: {}".format(result.stderr)
    )
    assert result.stdout.strip() == "", (
        "Expected empty stdout.\nStdout: {}".format(result.stdout)
    )


def test_models_list_empty_exits_0_with_advisory(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """models list exits 0 with advisory on stderr when provider returns empty model list."""

    monkeypatch.setattr(models_mod, "list_provider_models", lambda *a, **kw: [])
    result = runner.invoke(
        cli, ["models", "list", "--provider", "azure-openai", "--output", "json"]
    )
    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    )
    assert "azure-openai" in result.stderr, (
        f"Expected 'azure-openai' in stderr.\nStderr: {result.stderr}"
    )
    assert "no live model list" in result.stderr, (
        f"Expected 'no live model list' in stderr.\nStderr: {result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["models"] == [], (
        f"Expected empty models list.\nPayload: {payload}"
    )
