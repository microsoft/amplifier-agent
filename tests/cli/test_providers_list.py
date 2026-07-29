"""Tests for `amplifier-agent providers list` (spec section 6 public contract).

Verifies the JSON envelope shape, the table rendering path, and -- critically
-- that no credential material is ever emitted.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from tests.provider_env import clear_provider_env


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every provider-credential env var.

    The set is derived from ``PROVIDER_CREDENTIAL_VARS`` (see
    ``tests.provider_env``) so adding a provider cannot leave a hole here.
    """
    clear_provider_env(monkeypatch)


def test_providers_list_is_registered(runner: CliRunner) -> None:
    """`providers list --help` is reachable from the root CLI."""
    result = runner.invoke(cli, ["providers", "list", "--help"])
    assert result.exit_code == 0, result.output


def test_providers_list_json_shape(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """--json emits the schema_version=1 envelope with one row per KNOWN_PROVIDERS
    entry, and never leaks credential material."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-appear-in-output")

    result = runner.invoke(cli, ["providers", "list", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert isinstance(payload["providers"], list)

    from amplifier_agent_cli.provider_sources import KNOWN_PROVIDERS

    names = {row["name"] for row in payload["providers"]}
    assert names == set(KNOWN_PROVIDERS)

    for row in payload["providers"]:
        assert set(row.keys()) == {"name", "module", "resolvable", "source", "env_var"}
        assert isinstance(row["resolvable"], bool)

    anthropic_row = next(row for row in payload["providers"] if row["name"] == "anthropic")
    assert anthropic_row["resolvable"] is True
    assert anthropic_row["source"] == "env"
    assert anthropic_row["env_var"] == "ANTHROPIC_API_KEY"

    # No key material anywhere in the raw output.
    assert "sk-should-never-appear-in-output" not in result.output


def test_providers_list_ollama_default_not_resolvable(runner: CliRunner) -> None:
    """With nothing configured, ollama's row reports resolvable=False, source=default."""
    result = runner.invoke(cli, ["providers", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ollama_row = next(row for row in payload["providers"] if row["name"] == "ollama")
    assert ollama_row["resolvable"] is False
    assert ollama_row["source"] == "default"


def test_providers_list_table_output(runner: CliRunner) -> None:
    """--output table renders a 4-column PROVIDER/MODULE/RESOLVABLE/SOURCE table."""
    result = runner.invoke(cli, ["providers", "list", "--output", "table"])
    assert result.exit_code == 0, result.output
    assert "PROVIDER" in result.output
    assert "MODULE" in result.output
    assert "RESOLVABLE" in result.output
    assert "SOURCE" in result.output
    assert "anthropic" in result.output
