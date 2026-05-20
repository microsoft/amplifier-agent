"""Tests for Mode A single-turn run command (Task 8).

15 tests covering:
  1.  test_run_with_prompt_prints_json_to_stdout
  2.  test_run_passes_prompt_to_engine
  3.  test_run_y_flag_sets_approval_mode_yes
  4.  test_run_n_flag_sets_approval_mode_no
  5.  test_run_y_and_n_mutually_exclusive
  6.  test_run_default_approval_is_prompt_when_tty
  7.  test_run_default_approval_is_no_when_not_tty
  8.  test_run_quiet_flag_sets_display_quiet
  9.  test_run_verbose_flag_sets_display_verbose
  10. test_run_debug_flag_sets_display_debug
  11. test_run_session_id_and_resume_passed_to_engine
  12. test_run_fresh_flag_passed_to_engine
  13. test_run_missing_prompt_and_non_tty_fails_with_prompt_required
  14. test_run_no_provider_configured_errors
  15. test_run_engine_raising_aaa_error_returns_json_envelope
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from amplifier_agent_lib.protocol.errors import AaaError

# ---------------------------------------------------------------------------
# Provider env var constants
# ---------------------------------------------------------------------------

_PROVIDER_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_KEY", "OLLAMA_HOST")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_execute_turn(
    *,
    reply: str = "stub",
    raises: Exception | None = None,
) -> tuple[Any, list]:
    """Return (patch_target, captured_specs_list).

    Captures every _TurnSpec passed in so tests can assert on flag → spec mapping.
    """
    captured: list = []

    async def _fake(spec):
        captured.append(spec)
        if raises is not None:
            raise raises
        return {"reply": reply, "turnId": "turn-1"}

    return patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake), captured


def _set_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set ANTHROPIC_API_KEY so provider detection returns 'anthropic'."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    """Standard CliRunner for CLI tests."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Test 1: run with prompt prints JSON to stdout
# ---------------------------------------------------------------------------


def test_run_with_prompt_prints_json_to_stdout(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'run' with a prompt exits 0 and JSON reply is on stdout."""
    _set_anthropic(monkeypatch)
    patch_obj, _ = _patch_execute_turn(reply="hello!")
    with patch_obj:
        result = runner.invoke(cli, ["run", "hello!"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    parsed = json.loads(result.stdout)
    assert parsed["reply"] == "hello!"


# ---------------------------------------------------------------------------
# Test 2: run passes prompt to engine
# ---------------------------------------------------------------------------


def test_run_passes_prompt_to_engine(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_execute_turn is called with a spec whose prompt matches the CLI argument."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "do the thing"])
    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].prompt == "do the thing"


# ---------------------------------------------------------------------------
# Test 3: -y flag sets approval mode to 'yes'
# ---------------------------------------------------------------------------


def test_run_y_flag_sets_approval_mode_yes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing -y sets approval.mode == 'yes' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "-y"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "yes"


# ---------------------------------------------------------------------------
# Test 4: -n flag sets approval mode to 'no'
# ---------------------------------------------------------------------------


def test_run_n_flag_sets_approval_mode_no(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing -n sets approval.mode == 'no' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "-n"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "no"


# ---------------------------------------------------------------------------
# Test 5: -y and -n mutually exclusive
# ---------------------------------------------------------------------------


def test_run_y_and_n_mutually_exclusive(runner: CliRunner) -> None:
    """-y and -n together produce a usage error (exit 2)."""
    result = runner.invoke(cli, ["run", "test", "-y", "-n"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Test 6: default approval is 'prompt' when stdin is a TTY
# ---------------------------------------------------------------------------


def test_run_default_approval_is_prompt_when_tty(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With is_stdin_tty=True and no -y/-n, approval.mode is 'prompt'."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=True):
            result = runner.invoke(cli, ["run", "test"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "prompt"


# ---------------------------------------------------------------------------
# Test 7: default approval is 'no' when stdin is not a TTY
# ---------------------------------------------------------------------------


def test_run_default_approval_is_no_when_not_tty(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With is_stdin_tty=False and no -y/-n, approval.mode is 'no'."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
            result = runner.invoke(cli, ["run", "test"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "no"


# ---------------------------------------------------------------------------
# Test 8: --quiet sets display verbosity to 'quiet'
# ---------------------------------------------------------------------------


def test_run_quiet_flag_sets_display_quiet(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--quiet sets display.verbosity == 'quiet' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "--quiet"])
    assert result.exit_code == 0
    assert captured[0].display.verbosity == "quiet"


# ---------------------------------------------------------------------------
# Test 9: --verbose sets display verbosity to 'verbose'
# ---------------------------------------------------------------------------


def test_run_verbose_flag_sets_display_verbose(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--verbose sets display.verbosity == 'verbose' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "--verbose"])
    assert result.exit_code == 0
    assert captured[0].display.verbosity == "verbose"


# ---------------------------------------------------------------------------
# Test 10: --debug sets display verbosity to 'debug'
# ---------------------------------------------------------------------------


def test_run_debug_flag_sets_display_debug(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--debug sets display.verbosity == 'debug' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "--debug"])
    assert result.exit_code == 0
    assert captured[0].display.verbosity == "debug"


# ---------------------------------------------------------------------------
# Test 11: --session-id and --resume passed to Engine.boot
# ---------------------------------------------------------------------------


def test_run_session_id_and_resume_passed_to_engine(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--session-id and --resume appear in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "--session-id", "abc", "--resume"])
    assert result.exit_code == 0
    assert captured[0].session_id == "abc"
    assert captured[0].resume is True


# ---------------------------------------------------------------------------
# Test 12: --fresh flag passed to Engine.boot
# ---------------------------------------------------------------------------


def test_run_fresh_flag_passed_to_engine(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--fresh appears as True in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "--session-id", "abc", "--fresh"])
    assert result.exit_code == 0
    assert captured[0].fresh is True


# ---------------------------------------------------------------------------
# Test 13: missing prompt + non-TTY stdin emits 'prompt_required'
# ---------------------------------------------------------------------------


def test_run_missing_prompt_and_non_tty_fails_with_prompt_required(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No prompt + non-TTY stdin: exit 2 with 'prompt_required' on stderr."""
    with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
        result = runner.invoke(cli, ["run"])
    assert result.exit_code == 2
    assert "prompt_required" in result.stderr or "prompt" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test 14: no provider configured → JSON error on stdout, exit 1
# ---------------------------------------------------------------------------


def test_run_no_provider_configured_errors(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no provider env vars are set, exit 1 and JSON error.code on stdout."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(cli, ["run", "some prompt"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "provider_not_configured"


# ---------------------------------------------------------------------------
# Test 15: AaaError from engine → JSON envelope on stdout, exit 1
# ---------------------------------------------------------------------------


def test_run_engine_raising_aaa_error_returns_json_envelope(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AaaError raised by _execute_turn produces JSON error envelope on stdout, exit 1."""
    _set_anthropic(monkeypatch)
    patch_obj, _ = _patch_execute_turn(raises=AaaError(code="bundle_load_failed", message="bad bundle"))
    with patch_obj:
        result = runner.invoke(cli, ["run", "test"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "bundle_load_failed"
    assert parsed["error"]["message"] == "bad bundle"
