"""End-to-end smoke tests via the installed console-script subprocess.

Exercises the full path through real subprocesses (``uv run amplifier-agent ...``).
Tests cover console-script wiring, import-order, and load-time side effects that
CliRunner cannot catch.  We do NOT call real providers — all exercised paths are
pure-Python (help, version, doctor, config show, cache clear, the Mode-B stub,
prompt-required path).

8 tests:
  1.  test_version_via_console_script
  2.  test_help_via_console_script
  3.  test_doctor_runs_to_completion_when_provider_set
  4.  test_config_show_emits_valid_json_to_stdout
  5.  test_cache_clear_returns_zero
  6.  test_unknown_command_exits_2
  7.  test_run_with_no_prompt_and_piped_stdin_fails_with_prompt_required
  8.  test_run_stdio_phase_3_stub_exits_1
"""

from __future__ import annotations

import json
import os
import subprocess


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
# 8. run --stdio exits 0 on EOF (Mode B is implemented)
# ---------------------------------------------------------------------------


def test_run_stdio_exits_0_on_stdin_close() -> None:
    """``run --stdio`` exits 0 when stdin is closed immediately (EOF graceful exit).

    Mode B is fully implemented in Phase 3.  Providing empty stdin causes the
    asyncio stdio loop to see EOF on the first readline() and return exit_code 0.
    """
    result = _run("run", "--stdio", env={"ANTHROPIC_API_KEY": "sk-test"}, input_text="")
    assert result.returncode == 0, (
        f"Expected exit_code 0 on --stdio with empty stdin, got {result.returncode}. stderr: {result.stderr!r}"
    )
