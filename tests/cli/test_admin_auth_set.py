"""Tests for ``amplifier-agent auth set`` credential input paths.

Verifies the argv-free ``--stdin`` path (so wrappers never expose the secret
in the process list) alongside the backward-compatible positional argument.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))


def _stored_key(tmp_path) -> str:
    data = json.loads((tmp_path / "credentials.json").read_text())
    return data["providers"]["anthropic"]["api_key"]


def test_set_via_positional_still_works(runner: CliRunner, tmp_path) -> None:
    """Backward compatibility: positional key argument continues to work."""
    result = runner.invoke(cli, ["auth", "set", "anthropic", "sk-ant-positional"])
    assert result.exit_code == 0, result.output
    assert _stored_key(tmp_path) == "sk-ant-positional"


def test_set_via_stdin(runner: CliRunner, tmp_path) -> None:
    """--stdin reads the key from stdin and stores it (never on argv)."""
    result = runner.invoke(cli, ["auth", "set", "anthropic", "--stdin"], input="sk-ant-fromstdin\n")
    assert result.exit_code == 0, result.output
    # Trailing newline from echo/printf must be stripped.
    assert _stored_key(tmp_path) == "sk-ant-fromstdin"


def test_stdin_plus_positional_is_rejected(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["auth", "set", "anthropic", "sk-ant-x", "--stdin"], input="sk-ant-y\n")
    assert result.exit_code != 0
    assert "not both" in result.output.lower()


def test_no_key_is_rejected(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["auth", "set", "anthropic"])
    assert result.exit_code != 0
    assert "no api key" in result.output.lower()


# ---------------------------------------------------------------------------
# github-copilot refusal (temporary; see auth._CONFIG_CREDENTIAL_UNSUPPORTED)
# ---------------------------------------------------------------------------


def test_set_github_copilot_is_refused(runner: CliRunner, tmp_path) -> None:
    """Storing a Copilot token would be dead data, so the command refuses.

    The provider resolves its token from ``os.environ`` and ignores the
    ``api_key`` the agent injects into its mount config, so a stored
    credential makes ``auth list`` report it configured while the provider
    still cannot see it. Refusing beats succeeding and lying.
    """
    result = runner.invoke(cli, ["auth", "set", "github-copilot", "ghp-should-not-be-stored"])
    assert result.exit_code != 0
    assert not (tmp_path / "credentials.json").exists()


def test_github_copilot_refusal_names_the_env_vars(runner: CliRunner) -> None:
    """The error has to be actionable: name the vars, in priority order."""
    result = runner.invoke(cli, ["auth", "set", "github-copilot", "ghp-x"])
    output = result.output
    for var in ("COPILOT_AGENT_TOKEN", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        assert var in output
    assert "gh auth token" in output
    assert "temporary" in output.lower()


def test_github_copilot_refused_before_key_validation(runner: CliRunner) -> None:
    """The gate fires even without a key, so the user gets the real reason."""
    result = runner.invoke(cli, ["auth", "set", "github-copilot"])
    assert result.exit_code != 0
    assert "no api key" not in result.output.lower()
    assert "GITHUB_TOKEN" in result.output


@pytest.mark.parametrize("provider", ["anthropic", "openai", "azure-openai", "ollama"])
def test_other_providers_still_accepted(runner: CliRunner, tmp_path, provider: str) -> None:
    """The refusal is scoped to github-copilot only."""
    result = runner.invoke(cli, ["auth", "set", provider, "sk-value"])
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "credentials.json").read_text())
    assert data["providers"][provider]["api_key"] == "sk-value"
