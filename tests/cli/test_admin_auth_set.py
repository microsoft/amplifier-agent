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
