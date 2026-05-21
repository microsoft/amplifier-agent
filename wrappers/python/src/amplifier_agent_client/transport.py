"""NDJSON subprocess transport.

Spawns a child process and exchanges JSON frames over its stdio:
- start(): spawn the child process and begin reading stdout
- send(obj): write a JSON frame (NDJSON) to stdin
- frames(): async iterator yielding parsed JSON objects from stdout
- terminate(): send SIGTERM, wait up to 5s, fall back to SIGKILL

Defensive requirement (MCP-style tolerance): non-JSON stdout lines are logged
to the stderr sink (or sys.stderr) and dropped silently — never raised.

Matches engine's existing tolerance pattern at src/amplifier_agent_lib/jsonrpc.py.
Pattern reference: engine's NDJSON write side uses json.dumps(...) + '\\n'.

No JSON-RPC semantics here — that is Task 6.  Transport speaks bytes/objects.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator, Callable
from typing import Any


class Transport:
    """Subprocess transport: spawn a child process + NDJSON framing over stdio."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: str | None = None,
        stderr_sink: Callable[[str], None] | None = None,
    ) -> None:
        """Initialise transport options (does NOT start the process yet).

        Args:
            command: Executable to spawn (e.g. ``"cat"``, ``"sh"``).
            args: Arguments passed to the command.
            env: Environment variable overrides merged on top of ``os.environ``.
                 Pass ``{}`` to inherit the current environment without overrides.
            cwd: Optional working directory for the child process.
            stderr_sink: Optional callable receiving stderr lines from the child
                         and non-JSON drop warnings.  Defaults to sys.stderr.
        """
        self._command = command
        self._args = args
        self._env = env
        self._cwd = cwd
        self._stderr_sink = stderr_sink

        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Set once _read_stdout has drained all stdout data.
        self._stdout_done: asyncio.Event = asyncio.Event()
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the child process and start background I/O reader tasks."""
        merged_env = {**os.environ, **self._env}

        self._proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            cwd=self._cwd,
        )
        self._read_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def terminate(self) -> int:
        """Send SIGTERM to the child process; wait up to 5 s; fall back to SIGKILL.

        Returns:
            The process exit code (negative on Unix for signal-terminated processes,
            e.g. ``-15`` for SIGTERM).
        """
        assert self._proc is not None, "terminate() called before start()"

        try:
            self._proc.terminate()
        except ProcessLookupError:
            pass  # Process already exited; that's fine.

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()

        # Ensure background tasks finish cleanly (they stop once pipes close).
        _tasks = [t for t in (self._read_task, self._stderr_task) if t is not None]
        if _tasks:
            await asyncio.gather(*_tasks, return_exceptions=True)

        return self._proc.returncode  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(self, obj: dict[str, Any]) -> None:
        """Serialize *obj* as a single NDJSON line and write to stdin.

        Pattern: ``json.dumps(obj) + '\\n'`` (matches engine's NDJSON write side).
        """
        assert self._proc is not None, "send() called before start()"
        assert self._proc.stdin is not None

        data = (json.dumps(obj) + "\n").encode()
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    async def frames(self) -> AsyncGenerator[dict[str, Any], None]:
        """Async iterator yielding parsed JSON frames from the child's stdout.

        Implementation uses an asyncio.Queue with a 0.1 s poll timeout so
        that the iterator can detect when stdout has been fully consumed and
        exit cleanly without blocking indefinitely.

        The iterator exits when:
        - ``_stdout_done`` is set (all stdout data consumed by ``_read_stdout``)
        - AND the queue is empty.
        """
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                yield item
            except TimeoutError:
                if self._stdout_done.is_set() and self._queue.empty():
                    break

    # ------------------------------------------------------------------
    # Internal background tasks
    # ------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        """Read stdout line by line; JSON-parse; enqueue or drop silently."""
        assert self._proc is not None
        assert self._proc.stdout is not None

        async for raw_line in self._proc.stdout:
            line = raw_line.decode(errors="replace").rstrip("\r\n")
            if not line:
                continue
            try:
                obj: dict[str, Any] = json.loads(line)
                await self._queue.put(obj)
            except json.JSONDecodeError:
                msg = f"[transport] non-JSON stdout line dropped: {line}"
                if self._stderr_sink:
                    self._stderr_sink(msg)
                else:
                    print(msg, file=sys.stderr)

        # Signal that all stdout data has been processed.
        self._stdout_done.set()

    async def _drain_stderr(self) -> None:
        """Drain child stderr lines to the optional sink."""
        assert self._proc is not None
        if self._proc.stderr is None:
            return

        async for raw_line in self._proc.stderr:
            line = raw_line.decode(errors="replace").rstrip("\r\n")
            if self._stderr_sink:
                self._stderr_sink(line)
