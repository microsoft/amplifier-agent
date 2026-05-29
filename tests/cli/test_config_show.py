"""Tests for the config show admin verb (Task 6).

Verifies that `amplifier-agent config show`:
  - Outputs valid JSON.
  - Reports provider with value and source='env:<VAR>' when provider env vars are set.
  - Reports XDG_CONFIG_HOME with source='env:XDG_CONFIG_HOME' when set.
  - Reports xdg_config_home with source='default' when XDG_CONFIG_HOME is absent.
  - Returns provider.value=None and source='unset' when no provider env vars are set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_config_show_outputs_valid_json(runner: CliRunner, tmp_path: Path) -> None:
    """config show must emit valid JSON to stdout with exit code 0."""
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    state = tmp_path / "state"
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_CACHE_HOME": str(cache),
        "XDG_STATE_HOME": str(state),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, dict)


def test_config_show_reports_provider_from_env(runner: CliRunner, tmp_path: Path) -> None:
    """When ANTHROPIC_API_KEY is set and others are absent, provider reflects anthropic from env."""
    env = {
        "ANTHROPIC_API_KEY": "sk-test",
        # Explicitly unset the other provider keys so detection order is clean.
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_API_KEY": "",
        "AZURE_OPENAI_KEY": "",  # legacy alias
        "OLLAMA_HOST": "",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["provider"]["value"] == "anthropic"
    assert parsed["provider"]["source"] == "env:ANTHROPIC_API_KEY"


def test_config_show_reports_xdg_config_home_from_env(runner: CliRunner, tmp_path: Path) -> None:
    """When XDG_CONFIG_HOME is set, xdg_config_home.value matches it and source='env:XDG_CONFIG_HOME'."""
    cfg = tmp_path / "my_config"
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["xdg_config_home"]["value"] == str(cfg)
    assert parsed["xdg_config_home"]["source"] == "env:XDG_CONFIG_HOME"


def test_config_show_reports_default_when_env_absent(runner: CliRunner, tmp_path: Path) -> None:
    """When XDG_CONFIG_HOME is unset, xdg_config_home.source=='default'."""
    env = {
        "HOME": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-test",
        # Ensure XDG_CONFIG_HOME, XDG_CACHE_HOME, XDG_STATE_HOME are absent.
    }
    result = runner.invoke(cli, ["config", "show"], env=env, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["xdg_config_home"]["source"] == "default"


def test_config_show_handles_no_provider_configured(runner: CliRunner, tmp_path: Path) -> None:
    """When no provider env vars are set, exit 0, provider.value is None, source=='unset'."""
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        # Explicitly set all four provider keys to empty strings to unset them.
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_KEY": "",
        "OLLAMA_HOST": "",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["provider"]["value"] is None
    assert parsed["provider"]["source"] == "unset"
