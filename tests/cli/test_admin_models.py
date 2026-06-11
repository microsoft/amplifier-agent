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
