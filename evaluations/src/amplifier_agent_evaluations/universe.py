"""A thin wrapper over dtu_lite.lib, plus background commands that survive past an exec timeout."""

from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import TextIO

from dtu_lite import lib
from dtu_lite.schemas import DtuLiteError, ExecResult, Universe

__all__ = [
    "DtuLiteError",
    "destroy",
    "execute",
    "kill_background",
    "launch",
    "leftover_id",
    "pull",
    "push",
    "run_background",
]


class _ThreadStderr:
    """sys.stderr routed per thread, so parallel launches each keep their compose progress in their own log."""

    def __init__(self, fallback: TextIO) -> None:
        self.fallback = fallback
        self.local = threading.local()

    def write(self, text: str) -> int:
        return (getattr(self.local, "target", None) or self.fallback).write(text)

    def flush(self) -> None:
        (getattr(self.local, "target", None) or self.fallback).flush()

    def __getattr__(self, name: str):
        return getattr(self.fallback, name)


_ROUTER_LOCK = threading.Lock()


def launch(profile: Path, timeout_seconds: int, log: Path | None = None) -> Universe:
    """Launch and wait for health; dtu-lite's compose progress goes to `log` when given."""
    if log is None:
        return lib.launch(profile, timeout_seconds)
    with _ROUTER_LOCK:
        if not isinstance(sys.stderr, _ThreadStderr):
            sys.stderr = _ThreadStderr(sys.stderr)
    router = sys.stderr
    with log.open("a") as target:
        router.local.target = target
        try:
            return lib.launch(profile, timeout_seconds)
        finally:
            router.local.target = None


def leftover_id(error: DtuLiteError) -> str | None:
    """The id of a universe a failed launch left running, read from the error's remedy or message."""
    for text in (error.remedy, error.message):
        match = re.search(r"--id ([\w-]+)", text) or re.search(r"[Uu]niverse ([a-z0-9][\w-]*[a-z0-9])", text)
        if match:
            return match.group(1)
    return None


def execute(id: str, command: str, timeout_seconds: int = 300, workdir: str | None = None) -> ExecResult:
    return lib.execute(id, command, workdir=workdir, timeout_seconds=timeout_seconds)


def push(id: str, source: Path, destination: str) -> None:
    lib.push_files(id, source, destination)


def pull(id: str, source: str, destination: Path) -> None:
    lib.pull_files(id, source, destination)


def destroy(id: str) -> None:
    """Take the universe down and drop the image its build left behind, which dtu-lite keeps."""
    lib.destroy(id)
    subprocess.run(["docker", "image", "rm", "-f", f"{id}-twin"], capture_output=True, text=True, timeout=120)


def run_background(
    id: str, command: str, log: str, exit_file: str, timeout_seconds: int, poll_seconds: int = 10
) -> int | None:
    """Run `command` detached in its own process group and poll for its exit code; None when it did not finish in time.

    The group id is written to `<exit_file>.pid` so `kill_background` can stop the whole tree.
    """
    pid_file = f"{exit_file}.pid"
    detached = (
        f"rm -f {shlex.quote(exit_file)}; "
        f"setsid bash -lc {shlex.quote(command)} > {shlex.quote(log)} 2>&1 < /dev/null & "
        f"echo $! > {shlex.quote(pid_file)}; wait $!; echo $? > {shlex.quote(exit_file)}"
    )
    started = execute(id, f"nohup sh -c {shlex.quote(detached)} > /dev/null 2>&1 &", timeout_seconds=60)
    if started.exit_code != 0:
        raise RuntimeError(f"could not start background command: {started.stderr.strip()}")
    deadline = time.monotonic() + timeout_seconds
    while True:
        probe = execute(id, f"cat {shlex.quote(exit_file)} 2>/dev/null", timeout_seconds=60)
        if probe.exit_code == 0 and probe.stdout.strip():
            return int(probe.stdout.strip())
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)


def kill_background(id: str, exit_file: str) -> ExecResult:
    """TERM, then KILL, the process group `run_background` started for `exit_file`."""
    pid_file = shlex.quote(f"{exit_file}.pid")
    return execute(
        id,
        f"g=$(cat {pid_file}) && kill -TERM -- -$g 2>/dev/null; sleep 3; kill -KILL -- -$g 2>/dev/null; "
        f"if kill -0 -- -$g 2>/dev/null; then echo alive; exit 1; else echo stopped; fi",
        timeout_seconds=60,
    )
