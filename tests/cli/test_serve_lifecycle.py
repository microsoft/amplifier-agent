"""Tests for ``amplifier_agent_cli.admin.serve_lifecycle``.

Covers state-file IO helpers (write/read/remove), process-liveness helpers,
and the three CLI subcommands (status, stop, restart).

Isolation strategy: every test that touches the state file uses
``AMPLIFIER_AGENT_HOME`` env-var redirection so we never pollute the real
``~/.amplifier-agent/`` directory.  Tests that exercise PID logic spawn
throwaway subprocesses via ``subprocess.Popen("python -c ...")`` and clean
up after themselves.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.admin.serve_lifecycle import (
    SCHEMA_VERSION,
    _state_dir,
    _state_file,
    is_pid_alive,
    read_state_file,
    remove_state_file,
    restart_command,
    status_command,
    stop_command,
    write_state_file,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect AMPLIFIER_AGENT_HOME to a temp dir for the duration of each test."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def sample_state(isolated_home: Path) -> dict:
    """A minimal valid state payload (no file on disk; just the dict)."""
    return {
        "pid": os.getpid(),
        "started_at": "2026-06-21T19:30:00Z",
        "host": "127.0.0.1",
        "port": 9099,
        "api_key": "local-dev-secret",
        "workspace": "test-workspace",
        "host_config_path": None,
        "providers_summary": {"anthropic": 3, "openai": 6},
    }


# ---------------------------------------------------------------------------
# write_state_file
# ---------------------------------------------------------------------------


def test_write_state_file_creates_with_correct_modes(isolated_home: Path, sample_state: dict) -> None:
    """write_state_file creates serve.json at mode 0600; parent dir at mode 0700."""
    write_state_file(sample_state)

    sf = _state_file()
    assert sf.exists(), "State file was not created"

    file_mode = stat.S_IMODE(sf.stat().st_mode)
    assert file_mode == 0o600, f"Expected mode 0600 on file, got {oct(file_mode)}"

    dir_mode = stat.S_IMODE(_state_dir().stat().st_mode)
    assert dir_mode == 0o700, f"Expected mode 0700 on directory, got {oct(dir_mode)}"


def test_write_state_file_content_round_trips(isolated_home: Path, sample_state: dict) -> None:
    """Written payload round-trips through read_state_file (with schema_version injected)."""
    write_state_file(sample_state)
    result = read_state_file()
    assert result is not None
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["pid"] == sample_state["pid"]
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 9099
    assert result["workspace"] == "test-workspace"
    # api_key must round-trip but never appear in error output
    assert result["api_key"] == "local-dev-secret"
    assert result["providers_summary"] == {"anthropic": 3, "openai": 6}


def test_write_state_file_is_atomic(isolated_home: Path, sample_state: dict) -> None:
    """State file does not appear until os.replace completes.

    We simulate an interrupted write by patching os.replace to a no-op AFTER
    the tempfile is written, then verify the state file does not exist.
    Subsequently we let a real write complete and verify the file appears.
    """
    sf = _state_file()

    # First: intercept os.replace so the tempfile is created but never renamed.
    with patch("amplifier_agent_cli.admin.serve_lifecycle.os.replace"):
        write_state_file(sample_state)

    # The state file must NOT exist after the intercepted (no-op) rename.
    assert not sf.exists(), "State file appeared despite os.replace being no-op'd"

    # Real write succeeds.
    write_state_file(sample_state)
    assert sf.exists()


# ---------------------------------------------------------------------------
# read_state_file
# ---------------------------------------------------------------------------


def test_read_state_file_missing_returns_none(isolated_home: Path) -> None:
    """read_state_file returns None when the file does not exist."""
    assert read_state_file() is None


def test_read_state_file_unknown_schema_version_raises(isolated_home: Path) -> None:
    """read_state_file raises ClickException for unknown schema_version."""
    import click

    _state_dir().mkdir(parents=True, exist_ok=True)
    bad_payload = json.dumps({"schema_version": 999, "pid": 1})
    _state_file().write_text(bad_payload, encoding="utf-8")

    with pytest.raises(click.ClickException) as exc_info:
        read_state_file()

    assert "schema_version" in str(exc_info.value.message)


# ---------------------------------------------------------------------------
# remove_state_file
# ---------------------------------------------------------------------------


def test_remove_state_file_idempotent(isolated_home: Path, sample_state: dict) -> None:
    """remove_state_file is idempotent — calling it twice raises no exception."""
    write_state_file(sample_state)
    assert _state_file().exists()

    remove_state_file()
    assert not _state_file().exists()

    # Second call must not raise.
    remove_state_file()


# ---------------------------------------------------------------------------
# is_pid_alive
# ---------------------------------------------------------------------------


def test_is_pid_alive_running_process(isolated_home: Path) -> None:
    """is_pid_alive returns True for a live subprocess."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert is_pid_alive(proc.pid)
    finally:
        proc.kill()
        proc.wait()


def test_is_pid_alive_dead_process(isolated_home: Path) -> None:
    """is_pid_alive returns False after the process exits."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    assert not is_pid_alive(proc.pid)


def test_is_pid_alive_init(isolated_home: Path) -> None:
    """PID 1 (init / launchd) is always alive."""
    assert is_pid_alive(1)


# ---------------------------------------------------------------------------
# ``serve status``
# ---------------------------------------------------------------------------


def test_status_no_state_file_reports_not_running(isolated_home: Path) -> None:
    """``serve status`` exits 0 and reports 'not running' when no state file."""
    runner = CliRunner()
    result = runner.invoke(status_command, [])
    assert result.exit_code == 0, result.output
    assert "not running" in result.output


def test_status_stale_pid_self_cleans(isolated_home: Path) -> None:
    """``serve status`` removes a state file whose PID is gone and exits 0."""
    # Write a state file with a PID that definitely does not exist.
    dead_proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead_pid = dead_proc.pid
    dead_proc.wait(timeout=5)

    write_state_file(
        {
            "pid": dead_pid,
            "started_at": "2026-06-21T19:30:00Z",
            "host": "127.0.0.1",
            "port": 9099,
            "api_key": "local-dev-secret",
            "workspace": "test",
            "host_config_path": None,
            "providers_summary": {},
        }
    )
    assert _state_file().exists()

    runner = CliRunner()
    result = runner.invoke(status_command, [])
    assert result.exit_code == 0, result.output
    assert "stale" in result.output
    assert not _state_file().exists()


def test_status_running_and_reachable(isolated_home: Path, sample_state: dict) -> None:
    """``serve status`` reports 'running at http://...' with provider counts."""
    write_state_file(sample_state)

    models_response = MagicMock()
    models_response.raise_for_status = MagicMock()

    with (
        patch(
            "amplifier_agent_cli.admin.serve_lifecycle.is_pid_alive",
            return_value=True,
        ),
        patch(
            "amplifier_agent_cli.admin.serve_lifecycle.httpx.get",
            return_value=models_response,
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(status_command, [])

    assert result.exit_code == 0, result.output
    assert "running at http://127.0.0.1:9099/v1/" in result.output
    assert "workspace=test-workspace" in result.output
    # Total model count from sample_state: 3 + 6 = 9
    assert "9 total" in result.output
    assert "anthropic: 3" in result.output
    assert "openai: 6" in result.output


# ---------------------------------------------------------------------------
# ``serve stop``
# ---------------------------------------------------------------------------


def test_stop_no_state_file_exits_1(isolated_home: Path) -> None:
    """``serve stop`` exits 1 when there is no state file."""
    runner = CliRunner()
    result = runner.invoke(stop_command, [])
    assert result.exit_code == 1


def test_stop_graceful_sends_sigterm(isolated_home: Path) -> None:
    """``serve stop`` sends SIGTERM and exits 0 when the process exits cleanly."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_state_file(
        {
            "pid": proc.pid,
            "started_at": "2026-06-21T19:30:00Z",
            "host": "127.0.0.1",
            "port": 9099,
            "api_key": "local-dev-secret",
            "workspace": "test",
            "host_config_path": None,
            "providers_summary": {},
        }
    )

    try:
        runner = CliRunner()
        result = runner.invoke(stop_command, ["--timeout", "5"])
        assert result.exit_code == 0, result.output
        assert "stopped" in result.output
        # Reap the zombie so the PID is fully released before checking liveness.
        proc.wait(timeout=5)
        assert proc.returncode is not None, "Process should have exited"
        assert not _state_file().exists()
    finally:
        # Clean up if stop didn't kill it.
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_stop_force_sends_sigkill(isolated_home: Path) -> None:
    """``serve stop --force`` sends SIGKILL immediately."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_state_file(
        {
            "pid": proc.pid,
            "started_at": "2026-06-21T19:30:00Z",
            "host": "127.0.0.1",
            "port": 9099,
            "api_key": "local-dev-secret",
            "workspace": "test",
            "host_config_path": None,
            "providers_summary": {},
        }
    )

    try:
        runner = CliRunner()
        result = runner.invoke(stop_command, ["--force"])
        assert result.exit_code == 0, result.output
        assert "SIGKILL" in result.output
        # Reap the zombie so we can confirm the exit.
        proc.wait(timeout=5)
        assert proc.returncode is not None, "Process should have exited"
        assert not _state_file().exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_stop_timeout_escalates(isolated_home: Path) -> None:
    """``serve stop`` escalates to SIGKILL when the graceful window expires."""
    # A process that ignores SIGTERM.
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            ("import signal, time; signal.signal(signal.SIGTERM, lambda s, f: None); time.sleep(60)"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give the process time to register the SIGTERM handler.
    time.sleep(0.3)

    write_state_file(
        {
            "pid": proc.pid,
            "started_at": "2026-06-21T19:30:00Z",
            "host": "127.0.0.1",
            "port": 9099,
            "api_key": "local-dev-secret",
            "workspace": "test",
            "host_config_path": None,
            "providers_summary": {},
        }
    )

    try:
        runner = CliRunner()
        result = runner.invoke(stop_command, ["--timeout", "0.5"])
        assert result.exit_code == 0, result.output
        assert "SIGKILL" in result.output
        # Reap the zombie to confirm the exit.
        proc.wait(timeout=5)
        assert proc.returncode is not None, "Process should have exited"
        assert not _state_file().exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# ``serve restart``
# ---------------------------------------------------------------------------


def test_restart_no_state_file_exits_1(isolated_home: Path) -> None:
    """``serve restart`` exits 1 when there is no state file."""
    runner = CliRunner()
    result = runner.invoke(restart_command, [])
    assert result.exit_code == 1
    assert "nothing to restart" in result.output


def test_restart_invokes_stop_then_start(isolated_home: Path) -> None:
    """``serve restart`` assembles the correct command from state file args."""
    # Spawn a dummy long-lived process to act as the "old server".
    old_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    old_pid = old_proc.pid

    write_state_file(
        {
            "pid": old_pid,
            "started_at": "2026-06-21T19:30:00Z",
            "host": "127.0.0.1",
            "port": 9099,
            "api_key": "local-dev-secret",
            "workspace": "my-workspace",
            "host_config_path": "/tmp/cfg.json",
            "providers_summary": {"anthropic": 1},
        }
    )

    captured_cmd: list[list[str]] = []

    # Immediately write a fresh state file so restart_command thinks the
    # new server came up.
    def _fake_popen(cmd: list[str], **kwargs: object) -> MagicMock:
        captured_cmd.append(cmd)
        # Write the new state file so the restart polling loop exits quickly.
        write_state_file(
            {
                "pid": 99999,  # different from old_pid
                "started_at": "2026-06-21T20:00:00Z",
                "host": "127.0.0.1",
                "port": 9099,
                "api_key": "local-dev-secret",
                "workspace": "my-workspace",
                "host_config_path": "/tmp/cfg.json",
                "providers_summary": {"anthropic": 1},
            }
        )
        m = MagicMock()
        m.pid = 99999
        return m

    try:
        with patch(
            "amplifier_agent_cli.admin.serve_lifecycle.subprocess.Popen",
            side_effect=_fake_popen,
        ):
            runner = CliRunner()
            result = runner.invoke(restart_command, [])

        assert result.exit_code == 0, result.output
        assert "restarted" in result.output
        assert len(captured_cmd) == 1
        cmd = captured_cmd[0]

        # Must include the original launch args.
        assert "serve" in cmd
        assert "chat-completions" in cmd
        assert "--bind" in cmd
        assert "127.0.0.1" in cmd
        assert "--port" in cmd
        assert "9099" in cmd
        assert "--workspace" in cmd
        assert "my-workspace" in cmd
        assert "--config" in cmd
        assert "/tmp/cfg.json" in cmd

        # api_key must NOT appear in any readable error output or logs —
        # but it IS in the cmd (passed as a flag to the sub-process, same as
        # the original invocation). We just verify the key was forwarded.
        assert "--api-key" in cmd
    finally:
        if is_pid_alive(old_pid):
            old_proc.kill()
        old_proc.wait()
