"""State file management and lifecycle commands for ``amplifier-agent serve``.

Single global state file at ``~/.amplifier-agent/state/serve.json``. Records
the running server's PID, wire endpoint, credentials, workspace, and a
summary of served providers — enough for ``serve status / stop / restart``
to operate without needing the original invocation context.

The file is atomic-write (tempfile + os.replace), mode 0600, parent dir
0700. ``api_key`` is sensitive; never log it, never include it in error
messages, never leak it via process listings beyond the original invocation.

This module is the SINGLE owner of the file path and schema. Callers go
through ``read_state_file``, ``write_state_file``, and
``remove_state_file`` — never touch the path directly.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import click
import httpx

from amplifier_agent_lib.persistence import amplifier_agent_home

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
_STATE_FILE_MODE = 0o600
_STATE_DIR_MODE = 0o700

# Windows differs from POSIX in three ways that matter to this module:
#
#   - os.kill(pid, 0) is not a liveness probe. CPython's posixmodule maps
#     CTRL_C_EVENT and CTRL_BREAK_EVENT to GenerateConsoleCtrlEvent and every
#     other signal to TerminateProcess. Because wincon.h defines CTRL_C_EVENT
#     as 0, signal 0 takes the *first* branch: it delivers a real Ctrl+C to the
#     target's console process group rather than testing for existence. So the
#     "benign probe" is not benign -- it can kill the server, and can hit other
#     processes sharing that console group. See _is_pid_alive_windows.
#   - signal.SIGKILL does not exist (AttributeError on access). See _hard_kill.
#   - Detaching a child needs DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP;
#     start_new_session (setsid) is POSIX-only. See restart_command.
#
# File permissions are deliberately NOT keyed off this flag -- whether chmod
# can enforce a mode is a property of the filesystem, not the OS name. See
# _enforce_mode.
_IS_WINDOWS = os.name == "nt"


def _state_dir() -> Path:
    """Return the directory that holds ``serve.json``.

    Resolves via :func:`amplifier_agent_home` so tests can redirect with
    ``AMPLIFIER_AGENT_HOME``.
    """
    return amplifier_agent_home() / "state"


def _state_file() -> Path:
    """Return the canonical path of the state file."""
    return _state_dir() / "serve.json"


# ---------------------------------------------------------------------------
# File-mode helpers
# ---------------------------------------------------------------------------


def _enforce_mode(path: Path, mode: int, *, cannot_set: str, not_applied: str) -> None:
    """Apply ``mode`` to ``path`` and verify it stuck, else raise ``PermissionError``.

    The state file holds a plaintext ``api_key``, so on POSIX we refuse to
    write it unless the mode is genuinely enforced: chmod, then stat back and
    compare. Some networked and virtual filesystems silently ignore chmod, and
    a mode that did not stick is worse than useless -- it looks like
    protection that is not there.

    Windows is exempt from the *verification*, not from the intent. NTFS has
    no POSIX mode bits: chmod cannot produce 0o700/0o600, stat reports
    0o777/0o666, so the comparison could never pass and ``serve`` could never
    persist its state file on any Windows machine. There the protection
    boundary is the ACL on the user profile directory
    (``%USERPROFILE%\\.amplifier-agent``), which by default denies other
    standard users. We still call chmod -- it sets the owner-write bit and is
    harmless -- we just do not assert a POSIX guarantee the platform does not
    make.

    ``cannot_set`` and ``not_applied`` are the caller's message tails so each
    call site can name what it was protecting.
    """
    try:
        os.chmod(path, mode)
    except NotImplementedError as exc:
        if _IS_WINDOWS:
            return
        raise PermissionError(cannot_set) from exc
    if _IS_WINDOWS:
        return
    # Verify the mode was actually applied (some networked/virtual FSes ignore chmod).
    actual = stat.S_IMODE(os.stat(path).st_mode)
    if actual != mode:
        raise PermissionError(not_applied.format(actual=oct(actual)))


def _ensure_state_dir() -> Path:
    """Create the state directory with mode 0700, return its path.

    Raises ``PermissionError`` with a clear message if mode enforcement
    fails (rare; non-Unix filesystems that ignore chmod).
    """
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    _enforce_mode(
        d,
        _STATE_DIR_MODE,
        cannot_set=(
            f"Cannot set directory permissions on {d}. "
            "Your filesystem may not support Unix mode bits. "
            "The state file (which contains a sensitive api_key) cannot be "
            "written safely without mode 0700 on the parent directory."
        ),
        not_applied=(
            f"Failed to set mode 0700 on {d} (got {{actual}}). "
            "The state file contains a sensitive api_key and cannot be written "
            "safely without enforced directory permissions."
        ),
    )
    return d


# ---------------------------------------------------------------------------
# Public IO helpers
# ---------------------------------------------------------------------------


def write_state_file(payload: dict[str, Any]) -> None:
    """Write ``payload`` atomically to the state file (mode 0600, dir 0700).

    Uses a NamedTemporaryFile in the *same directory* as the target so
    that ``os.replace`` is an atomic rename on POSIX (same filesystem).
    The tempfile is chmod'd to 0600 *before* the rename so the sensitive
    ``api_key`` field is never visible at a more-permissive mode.

    Raises ``PermissionError`` if mode enforcement fails (non-Unix FS).
    """
    d = _ensure_state_dir()
    payload = {**payload, "schema_version": SCHEMA_VERSION}
    encoded = json.dumps(payload, indent=2).encode("utf-8")

    # Write into a tempfile in the same directory so os.replace is atomic.
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".serve-", suffix=".json.tmp")
    try:
        # Restrict the mode *before* the sensitive payload is written, so the
        # api_key is never on disk at a more-permissive mode.
        _enforce_mode(
            Path(tmp_path),
            _STATE_FILE_MODE,
            cannot_set=(
                f"Cannot set mode 0600 on {tmp_path}. "
                "Your filesystem may not support Unix mode bits. "
                "Refusing to write api_key in plaintext without permission enforcement."
            ),
            not_applied=(
                f"Failed to set mode 0600 on {tmp_path} (got {{actual}}). "
                "Refusing to write api_key in plaintext without enforced file permissions."
            ),
        )
        os.write(fd, encoded)
    finally:
        os.close(fd)

    os.replace(tmp_path, _state_file())


def read_state_file() -> dict[str, Any] | None:
    """Read and parse the state file.

    Returns ``None`` if the file does not exist.
    Raises ``click.ClickException`` on an unknown schema version so the
    user gets a clear error with a remediation path.
    """
    sf = _state_file()
    if not sf.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise click.ClickException(
            f"State file {sf} is unreadable or corrupt: {exc}. "
            "Remove it manually and re-run 'amplifier-agent serve chat-completions' to start fresh."
        ) from exc
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise click.ClickException(
            f"State file {sf} has schema_version={version!r} but this version of "
            f"amplifier-agent only understands schema_version={SCHEMA_VERSION}. "
            "Remove the file manually: "
            f"rm {sf}"
        )
    return data


def remove_state_file() -> None:
    """Remove the state file if it exists (idempotent)."""
    _state_file().unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Process-liveness helpers
# ---------------------------------------------------------------------------


def _is_pid_alive_windows(pid: int) -> bool:
    """Windows liveness check that does NOT signal or terminate the process.

    ``os.kill(pid, 0)`` cannot be used here. CPython maps CTRL_C_EVENT and
    CTRL_BREAK_EVENT to ``GenerateConsoleCtrlEvent`` and every other signal to
    ``TerminateProcess``; since ``CTRL_C_EVENT == 0``, signal 0 takes the first
    branch and delivers a real Ctrl+C to the target's console process group.
    Either branch can kill what we only meant to observe.

    So we open a handle and poll it instead: ``WaitForSingleObject(handle, 0)``
    returns WAIT_TIMEOUT while the process is alive and WAIT_OBJECT_0 once it
    has exited. ACCESS_DENIED from OpenProcess means the process exists but is
    owned by another user, mirroring the POSIX ``PermissionError`` -> alive case.
    """
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    ERROR_ACCESS_DENIED = 5

    # use_last_error routes GetLastError through ctypes' own thread-local copy,
    # captured immediately after the call. Calling kernel32.GetLastError()
    # directly would read the error state *after* ctypes' own bookkeeping and
    # can report a stale or cleared value.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    # Declare signatures explicitly. Without restype, ctypes assumes c_int and
    # truncates the 64-bit HANDLE, so CloseHandle would be handed a bogus value
    # (leaking the real handle) and a valid handle could read as falsy.
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED  # type: ignore[attr-defined]
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _hard_kill(pid: int) -> None:
    """Forcefully terminate ``pid`` (the SIGKILL equivalent).

    Windows has no ``signal.SIGKILL`` -- accessing the attribute raises
    ``AttributeError``, so it must be probed with ``hasattr`` rather than
    caught. SIGTERM is the right fallback there: it is neither CTRL_C_EVENT
    nor CTRL_BREAK_EVENT, so CPython routes it to ``TerminateProcess``, which
    is the unconditional kill this function promises. On POSIX the two signals
    keep their usual meanings and SIGKILL is used directly.
    """
    sig = signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
    os.kill(pid, sig)


def is_pid_alive(pid: int) -> bool:
    """Return True if process ``pid`` exists and is signalable.

    On POSIX, uses ``os.kill(pid, 0)`` (signal 0 checks existence without
    delivering a signal). ``PermissionError`` means the process exists but we
    don't own it — still alive. ``ProcessLookupError`` means it is gone.

    On Windows, delegates to :func:`_is_pid_alive_windows`: signal 0 is not a
    no-op probe there (it is CTRL_C_EVENT, which CPython delivers as a real
    console Ctrl+C), so the POSIX idiom would signal the process it is meant
    to be silently observing.
    """
    if _IS_WINDOWS:
        return _is_pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists, owned by another user.
        return True


def wait_for_exit(pid: int, timeout: float) -> bool:
    """Poll until ``pid`` exits or ``timeout`` seconds elapse.

    Returns ``True`` if the process exited, ``False`` on timeout.
    Polls every 100 ms.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.1)
    return not is_pid_alive(pid)


# ---------------------------------------------------------------------------
# ``serve status``
# ---------------------------------------------------------------------------


@click.command(name="status")
def status_command() -> None:
    """Report whether the chat-completions server is running.

    Checks the state file, validates the recorded PID is still alive, and
    probes ``GET /v1/models`` over the wire to confirm the server is
    responding. Exits 0 when healthy, 1 when the server cannot be reached.
    """
    state = read_state_file()

    if state is None:
        click.echo("amplifier-agent serve: not running")
        raise SystemExit(0)

    pid: int = state["pid"]

    if not is_pid_alive(pid):
        click.echo(f"amplifier-agent serve: stale state file (PID {pid} no longer exists) — cleaned")
        remove_state_file()
        raise SystemExit(0)

    host: str = state["host"]
    port: int = state["port"]
    api_key: str = state["api_key"]
    workspace: str = state.get("workspace") or "(cwd-derived)"
    providers_summary: dict[str, int] = state.get("providers_summary", {})

    # Probe the wire endpoint.
    try:
        resp = httpx.get(
            f"http://{host}:{port}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=2.0,
        )
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        click.echo(f"amplifier-agent serve: running (PID {pid}) — but http://{host}:{port}/v1/models not responding")
        raise SystemExit(1) from None

    total = sum(providers_summary.values())
    click.echo(f"amplifier-agent serve: running at http://{host}:{port}/v1/  (PID {pid}, workspace={workspace})")
    click.echo(f"  models: {total} total")
    click.echo("  by provider:")
    for provider_id, count in providers_summary.items():
        click.echo(f"    {provider_id}: {count}")

    raise SystemExit(0)


# ---------------------------------------------------------------------------
# ``serve stop``
# ---------------------------------------------------------------------------


@click.command(name="stop")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the graceful wait and send SIGKILL immediately.",
)
@click.option(
    "--timeout",
    "timeout_s",
    default=5.0,
    show_default=True,
    type=float,
    metavar="SECONDS",
    help="Graceful-exit window before escalating to SIGKILL.",
)
def stop_command(force: bool, timeout_s: float) -> None:
    """Stop the running chat-completions server.

    Sends SIGTERM and waits up to ``--timeout`` seconds for a clean exit.
    Escalates to SIGKILL on timeout or when ``--force`` is given.
    Exits 0 on success, 1 when there is nothing to stop.
    """
    state = read_state_file()

    if state is None:
        click.echo("amplifier-agent serve: not running", err=True)
        raise SystemExit(1)

    pid: int = state["pid"]

    if not is_pid_alive(pid):
        click.echo(
            f"amplifier-agent serve: stale state file (PID {pid} no longer exists) — cleaning",
            err=True,
        )
        remove_state_file()
        raise SystemExit(0)

    if force:
        _hard_kill(pid)
        wait_for_exit(pid, timeout=2.0)
        remove_state_file()
        click.echo(f"amplifier-agent serve: stopped (SIGKILL, PID {pid})")
        raise SystemExit(0)

    # Graceful path: SIGTERM → wait → escalate if needed.
    os.kill(pid, signal.SIGTERM)
    if wait_for_exit(pid, timeout=timeout_s):
        # SIGTERM handler should have removed the state file; double-check.
        if _state_file().exists():
            remove_state_file()
        click.echo(f"amplifier-agent serve: stopped (SIGTERM, PID {pid})")
        raise SystemExit(0)

    # Graceful window expired — escalate.
    _hard_kill(pid)
    wait_for_exit(pid, timeout=2.0)
    remove_state_file()
    click.echo(
        f"amplifier-agent serve: stopped (SIGTERM timed out after {timeout_s}s, escalated to SIGKILL, PID {pid})",
        err=True,
    )
    raise SystemExit(0)


# ---------------------------------------------------------------------------
# ``serve restart``
# ---------------------------------------------------------------------------


def _resolve_amplifier_agent_executable() -> list[str]:
    """Resolve the ``amplifier-agent`` executable path for subprocess re-launch.

    Resolution order:
    1. ``shutil.which("amplifier-agent")`` — installed entry point on PATH.
    2. ``[sys.executable, "-m", "amplifier_agent_cli"]`` — editable / bare
       checkout fallback.
    """
    exe = shutil.which("amplifier-agent")
    if exe:
        return [exe]
    return [sys.executable, "-m", "amplifier_agent_cli"]


@click.command(name="restart")
def restart_command() -> None:
    """Restart the chat-completions server using the stored launch args.

    Reads the host, port, api-key, workspace, and host_config_path from the
    existing state file, stops the running server, and re-launches it as a
    detached background process. Waits up to 30 s for the new state file to
    appear (confirming successful startup) before reporting success.

    Exits 1 when there is nothing to restart or the new server does not
    become ready within 30 s.
    """
    state = read_state_file()

    if state is None:
        click.echo("amplifier-agent serve: not running — nothing to restart", err=True)
        raise SystemExit(1)

    # Capture launch args before stopping (stop will remove the state file).
    host: str = state["host"]
    port: int = state["port"]
    api_key: str = state["api_key"]
    workspace: str | None = state.get("workspace")
    host_config_path: str | None = state.get("host_config_path")
    old_pid: int = state["pid"]

    # Stop the running server via the graceful stop path.
    os.kill(old_pid, signal.SIGTERM) if is_pid_alive(old_pid) else None
    if not wait_for_exit(old_pid, timeout=5.0):
        if is_pid_alive(old_pid):
            _hard_kill(old_pid)
            wait_for_exit(old_pid, timeout=2.0)
    remove_state_file()

    # Reconstruct launch command from stored args.
    cmd = [
        *_resolve_amplifier_agent_executable(),
        "serve",
        "chat-completions",
        "--bind",
        host,
        "--port",
        str(port),
        "--api-key",
        api_key,
    ]
    if workspace:
        cmd.extend(["--workspace", workspace])
    if host_config_path:
        cmd.extend(["--config", host_config_path])

    # Launch detached — stdout/stderr go to the null device; the server writes
    # its own logs via uvicorn's log machinery. Detach differently per platform:
    # start_new_session (setsid) is a POSIX no-op on Windows, where real
    # detachment requires DETACHED_PROCESS + a new process group.
    devnull = open(os.devnull, "wb")
    detach_kwargs: dict[str, Any] = {}
    if _IS_WINDOWS:
        detach_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        detach_kwargs["start_new_session"] = True
    subprocess.Popen(
        cmd,
        stdout=devnull,
        stderr=devnull,
        **detach_kwargs,
    )

    # Wait for the new state file to appear, indicating a successful lifespan.
    deadline = time.monotonic() + 30.0
    sf = _state_file()
    while time.monotonic() < deadline:
        if sf.exists():
            try:
                new_state = read_state_file()
            except click.ClickException:
                new_state = None
            if new_state is not None and new_state.get("pid") != old_pid:
                new_pid = new_state["pid"]
                click.echo(f"amplifier-agent serve: restarted at http://{host}:{port}/v1/  (new PID {new_pid})")
                raise SystemExit(0)
        time.sleep(0.2)

    click.echo(
        "amplifier-agent serve: restart launched but new server did not become ready within 30s — check logs",
        err=True,
    )
    raise SystemExit(1)
