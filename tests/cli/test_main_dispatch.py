"""Tests for the amplifier-agent CLI dispatcher (__main__.py).

These tests verify the click group wiring, --version, --help,
and subcommand dispatch stubs using click.testing.CliRunner.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from amplifier_agent_cli import __version__
from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_version_flag_prints_version_and_exits_0(runner: CliRunner) -> None:
    """--version should print the package version and exit 0."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_flag_prints_help_and_exits_0(runner: CliRunner) -> None:
    """--help should print usage information and exit 0."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output or "amplifier-agent" in result.output.lower()


def test_no_subcommand_prints_help_and_exits_0(runner: CliRunner) -> None:
    """Invoking the CLI with no subcommand should print help and exit 0."""
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_unknown_subcommand_exits_2(runner: CliRunner) -> None:
    """An unknown subcommand should cause click to exit with code 2 (usage error)."""
    result = runner.invoke(cli, ["bogus-command"])
    assert result.exit_code == 2


def test_help_lists_run_subcommand(runner: CliRunner) -> None:
    """--help output should list the 'run' subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_help_lists_doctor_subcommand(runner: CliRunner) -> None:
    """--help output should list the 'doctor' subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_help_lists_config_subgroup(runner: CliRunner) -> None:
    """--help output should list the 'config' subgroup."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.output


def test_help_lists_cache_subgroup(runner: CliRunner) -> None:
    """--help output should list the 'cache' subgroup."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "cache" in result.output


def test_config_subgroup_shows_show_command(runner: CliRunner) -> None:
    """'config --help' should list the 'show' command."""
    result = runner.invoke(cli, ["config", "--help"])
    assert result.exit_code == 0
    assert "show" in result.output


def test_cache_subgroup_shows_clear_command(runner: CliRunner) -> None:
    """'cache --help' should list the 'clear' command."""
    result = runner.invoke(cli, ["cache", "--help"])
    assert result.exit_code == 0
    assert "clear" in result.output
