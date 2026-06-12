"""Tests for CLI argv-level flag-conflict detection (Phase 2, Task 2.2).

Verifies that incompatible flag combinations are rejected at parse time
(before any engine boot) with exit code 2 and a descriptive UsageError.

Test cases:
  1. test_quiet_and_verbose_are_mutually_exclusive
  2. test_quiet_and_debug_are_mutually_exclusive
  3. test_resume_and_fresh_are_mutually_exclusive
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    """Standard CliRunner for CLI tests."""
    return CliRunner()


# ---------------------------------------------------------------------------
# --quiet conflicts with -v/--verbose and --debug
# ---------------------------------------------------------------------------


def test_quiet_and_verbose_are_mutually_exclusive(runner: CliRunner) -> None:
    """--quiet and -v together produce a usage error (exit 2)."""
    result = runner.invoke(cli, ["run", "test prompt", "--quiet", "-v"])
    assert result.exit_code == 2
    assert "--quiet" in result.output
    assert "verbose" in result.output.lower() or "verbosity" in result.output.lower()


def test_quiet_and_debug_are_mutually_exclusive(runner: CliRunner) -> None:
    """--quiet and --debug together produce a usage error (exit 2)."""
    result = runner.invoke(cli, ["run", "test prompt", "--quiet", "--debug"])
    assert result.exit_code == 2
    assert "--quiet" in result.output
    assert "debug" in result.output.lower() or "verbosity" in result.output.lower()


# ---------------------------------------------------------------------------
# --resume and --fresh are mutually exclusive
# ---------------------------------------------------------------------------


def test_resume_and_fresh_are_mutually_exclusive(runner: CliRunner) -> None:
    """--resume and --fresh together produce a usage error (exit 2)."""
    result = runner.invoke(cli, ["run", "test prompt", "--resume", "--fresh"])
    assert result.exit_code == 2
    assert "--resume" in result.output
    assert "--fresh" in result.output
