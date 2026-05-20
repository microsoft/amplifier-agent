"""End-to-end smoke tests via the installed console-script subprocess.

Exercises the full path through real subprocesses (``uv run amplifier-agent ...``).
Tests cover console-script wiring, import-order, and load-time side effects that
CliRunner cannot catch.  We do NOT call real providers — all exercised paths are
pure-Python (help, version, doctor, config show, cache clear, prompt-required path).

10 tests:
  1.  test_version_via_console_script
  2.  test_help_via_console_script
  3.  test_doctor_runs_to_completion_when_provider_set
  4.  test_config_show_emits_valid_json_to_stdout
  5.  test_cache_clear_returns_zero
  6.  test_unknown_command_exits_2
  7.  test_run_with_no_prompt_and_piped_stdin_fails_with_prompt_required
  8.  test_stdio_flag_removed
  9.  test_phase_2_0c_exit_gate_verify_check_hooks_exits_0
  10. test_phase_2_0c_exit_gate_real_turn_emits_result_events
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest


def _run(
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``amplifier-agent`` via ``uv run`` in a real subprocess.

    Parameters
    ----------
    *args:
        Extra arguments forwarded to the console-script (e.g. ``"--version"``).
    env:
        Optional dict of environment variable *overrides*.  Merged on top of
        ``os.environ.copy()`` so the child inherits the full current environment
        plus the requested overrides.
    input_text:
        If given, passed as *stdin* to the subprocess (simulates piped input).
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["uv", "run", "amplifier-agent", *args],
        capture_output=True,
        text=True,
        env=merged_env,
        input=input_text,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# 1. --version
# ---------------------------------------------------------------------------


def test_version_via_console_script() -> None:
    """Console-script is installed and ``--version`` prints the program name."""
    result = _run("--version")
    assert result.returncode == 0
    assert "amplifier-agent" in result.stdout.lower()


# ---------------------------------------------------------------------------
# 2. --help
# ---------------------------------------------------------------------------


def test_help_via_console_script() -> None:
    """``--help`` exits 0 and lists the ``run`` and ``doctor`` subcommands."""
    result = _run("--help")
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "doctor" in result.stdout


# ---------------------------------------------------------------------------
# 3. doctor
# ---------------------------------------------------------------------------


def test_doctor_runs_to_completion_when_provider_set() -> None:
    """``doctor`` completes and mentions python; exit 0 or 1 (sandbox tolerance)."""
    result = _run("doctor", env={"ANTHROPIC_API_KEY": "sk-test"})
    # Exit 0 on fully-healthy systems; exit 1 tolerated for sandboxes where
    # ~/.cache (or similar) is unwritable.
    assert result.returncode in (0, 1)
    assert "python" in result.stdout.lower()


# ---------------------------------------------------------------------------
# 4. config show
# ---------------------------------------------------------------------------


def test_config_show_emits_valid_json_to_stdout() -> None:
    """``config show`` exits 0 and prints valid JSON with a ``provider`` key."""
    result = _run("config", "show", env={"ANTHROPIC_API_KEY": "sk-test"})
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "provider" in data


# ---------------------------------------------------------------------------
# 5. cache clear
# ---------------------------------------------------------------------------


def test_cache_clear_returns_zero() -> None:
    """``cache clear`` exits 0 (idempotent — nothing to clear is fine)."""
    result = _run("cache", "clear", env={"ANTHROPIC_API_KEY": "sk-test"})
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# 6. unknown command
# ---------------------------------------------------------------------------


def test_unknown_command_exits_2() -> None:
    """An unknown subcommand causes click to exit with code 2 (usage error)."""
    result = _run("bogus-subcommand")
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# 7. run — no prompt, piped empty stdin
# ---------------------------------------------------------------------------


def test_run_with_no_prompt_and_piped_stdin_fails_with_prompt_required() -> None:
    """``run`` with no PROMPT argument and piped empty stdin exits 2 with 'prompt' message."""
    result = _run("run", env={"ANTHROPIC_API_KEY": "sk-test"}, input_text="")
    assert result.returncode == 2
    combined = (result.stderr + result.stdout).lower()
    assert "prompt" in combined


# ---------------------------------------------------------------------------
# 8. run --stdio is removed (Mode B deleted)
# ---------------------------------------------------------------------------


def test_stdio_flag_removed() -> None:
    result = _run("run", "--stdio", env={"ANTHROPIC_API_KEY": "sk-test"}, input_text="")
    assert result.returncode == 2
    assert "no such option" in result.stderr.lower() or "unrecognized" in result.stderr.lower()


# ---------------------------------------------------------------------------
# 9. Phase 2.0c exit gate — verify --check-hooks exits 0
# ---------------------------------------------------------------------------


def test_phase_2_0c_exit_gate_verify_check_hooks_exits_0() -> None:
    """Phase 2.0c exit gate: verify --check-hooks must exit 0 with hook coverage confirmation.

    Verifies that the streaming hook exposes the minimum required wire events
    and that the verify command reports success.
    """
    result = _run("verify", "--check-hooks", env={"ANTHROPIC_API_KEY": "sk-test"})
    assert result.returncode == 0
    assert "minimum-set" in result.stdout.lower() or "ok" in result.stdout.lower()


# ---------------------------------------------------------------------------
# 10. Phase 2.0c exit gate — real foundation turn emits result events
# ---------------------------------------------------------------------------


def test_phase_2_0c_exit_gate_real_turn_emits_result_events() -> None:
    """Phase 2.0c exit gate: real-foundation single turn emits ≥1 result/delta and exactly 1 result/final.

    Skipped when ANTHROPIC_API_KEY is not set in the environment.
    Verifies that the streaming hook correctly translates kernel events into
    display events that reach the CliDisplaySystem.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping real-turn test")

    result = _run("run", "Say hi in three words.", "--verbose")

    stderr_lines = result.stderr.splitlines()
    delta_lines = [line for line in stderr_lines if "[result/delta]" in line]
    final_lines = [line for line in stderr_lines if "[result/final]" in line]

    last_30 = "\n".join(stderr_lines[-30:])
    assert len(delta_lines) >= 1, (
        f"Expected ≥1 [result/delta] lines, got {len(delta_lines)}\nLast 30 stderr lines:\n{last_30}"
    )
    assert len(final_lines) == 1, (
        f"Expected exactly 1 [result/final] line, got {len(final_lines)}\nLast 30 stderr lines:\n{last_30}"
    )
