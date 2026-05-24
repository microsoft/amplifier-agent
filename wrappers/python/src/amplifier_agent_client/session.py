"""SessionHandle — subprocess driver for Mode A v2 (A3').

Per the 2026-05-24 Mode A pivot amendment §5.2: each ``submit()`` call spawns
a fresh ``amplifier-agent run`` subprocess with the assembled argv. The async
iterable yields:

  1. ``{"type": "init", "sessionId": ...}`` — yielded SYNCHRONOUSLY before
     the subprocess is spawned (SC-1: no race window with the activity ticker).
  2. ``{"type": "activity"}`` — yielded every 2 seconds while the subprocess
     is alive (preserves NC's stuck-detection signal without engine-side
     cooperation).
  3. ``{"type": "result", "text": ...}`` or ``{"type": "error", ...}`` —
     yielded once when the subprocess exits (``parse_run_output`` applied to
     stdout/stderr/exit) OR when the configured ``timeout_ms`` elapses
     (synthesized ``engine_hung``).

Lifecycle:
  - ``submit()`` is one-shot per session (D10). A second call raises
    ``AaaError(lifecycle_unsupported)``.
  - ``cancel()`` SIGTERMs the whole process group (SC-B) via
    ``os.killpg(os.getpgid(proc.pid), signal.SIGTERM)``, waits up to 5s, then
    SIGKILLs if the engine has not exited. It also unlinks any MCP spill
    file created on this turn (CR-A cleanup).
  - ``dispose()`` is a synonym for ``cancel()``.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from amplifier_agent_client.argv_builder import assemble_argv
from amplifier_agent_client.mcp_spill import cleanup_spill_file, resolve_mcp_servers_flag
from amplifier_agent_client.run_output_parser import STDERR_TAIL_BYTES, parse_run_output
from amplifier_agent_client.types import DisplayEvent

#: Default subprocess timeout: 10 minutes.
DEFAULT_TIMEOUT_MS = 10 * 60 * 1000

#: Activity ticker interval: 2 seconds (NC stuck-detection has 10s threshold).
_ACTIVITY_TICK_MS = 2000

#: Grace window between SIGTERM and SIGKILL in ``cancel()``.
_SIGKILL_GRACE_MS = 5000


class AaaError(Exception):
    """Typed error for AaA wrapper lifecycle and protocol violations.

    Mirrors the TypeScript ``AaaError`` from session.ts. The positional
    ``(code, remediation)`` signature is preserved for backward compatibility.
    """

    def __init__(
        self,
        code: str,
        remediation: str | None = None,
        *,
        classification: str | None = None,
        severity: str | None = None,
        correlation_id: str | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(remediation or code)
        self.code = code
        self.remediation = remediation
        self.classification = classification
        self.severity = severity
        self.correlation_id = correlation_id
        self.stderr_tail = stderr_tail


@dataclass
class EngineInfo:
    """Info returned by ``SessionHandle.get_engine_info()`` (D5)."""

    binary_path: str
    protocol_version: str
    engine_version: str = ""
    bundle_digest: str = ""


@dataclass
class SessionHandleParams:
    """Parameters for constructing a ``SessionHandle`` (amendment §5.2).

    All session config is captured up-front and stored as instance state.
    The subprocess is not spawned until ``submit()`` is called.
    """

    binary_path: str
    session_id: str
    subprocess_env: dict[str, str]
    protocol_version: str
    resume: bool = False
    cwd: str | None = None
    mcp_servers: dict[str, dict[str, Any]] | None = None
    host_capabilities: dict[str, Any] | None = None
    env_allowlist: list[str] = field(default_factory=list)
    env_extra: dict[str, str] = field(default_factory=dict)
    provider_override: str | None = None
    allow_protocol_skew: bool = False
    timeout_ms: int = DEFAULT_TIMEOUT_MS


def _stderr_tail_of(stderr: str) -> str | None:
    """Last STDERR_TAIL_BYTES chars of ``stderr``, or None if empty."""
    if not stderr:
        return None
    if len(stderr) <= STDERR_TAIL_BYTES:
        return stderr
    return stderr[-STDERR_TAIL_BYTES:]


class SessionHandle:
    """One-shot session handle that drives the engine subprocess."""

    def __init__(self, params: SessionHandleParams) -> None:
        self._params = params
        self._submitted = False
        self._subprocess: asyncio.subprocess.Process | None = None
        self._mcp_spill_path: str | None = None
        # Strong references to fire-and-forget background tasks (RUF006).
        self._background_tasks: set[asyncio.Task[Any]] = set()
        # engine_version / bundle_digest are populated lazily from the JSON
        # envelope's ``metadata`` field once it arrives (post-submit).
        self._engine_info = EngineInfo(
            binary_path=params.binary_path,
            protocol_version=params.protocol_version,
            engine_version="",
            bundle_digest="",
        )

    def get_engine_info(self) -> EngineInfo:
        """Return resolved engine metadata (D5)."""
        return self._engine_info

    def submit(self, prompt: str) -> AsyncIterator[DisplayEvent]:
        """Submit a prompt and return an AsyncIterator of DisplayEvents.

        One-shot per session (D10): raises ``AaaError(lifecycle_unsupported)``
        on second call.
        """
        if self._submitted:
            raise AaaError(
                "lifecycle_unsupported",
                "SessionHandle.submit() is one-shot per session (D10); already submitted",
            )
        self._submitted = True
        return self._make_iterable(prompt)

    async def _make_iterable(self, prompt: str) -> AsyncIterator[DisplayEvent]:
        """Async generator implementing the §5.2 iterable behavior.

        (i)   yield ``{"type": "init", "sessionId": ...}`` synchronously (SC-1);
        (ii)  CR-A: resolve ``--mcp-servers`` flag (spill if env-bearing);
        (iii) build argv via ``assemble_argv``;
        (iv)  SC-B: spawn with ``start_new_session=True`` so PID == PGID
              for group signals;
        (v)   accumulate stdout/stderr from streams;
        (vi)  start a 2s activity ticker → queue;
        (vii) race exit-wait vs timeout;
              on timeout: synthesize ``engine_hung`` then cancel();
              on exit:    parse_run_output({stdout, stderr, exitCode});
        (viii) cleanup spill file after exit;
        (ix)  drain queue until the final event is yielded.
        """
        # (i) SC-1: yield init synchronously, BEFORE any async work.
        yield {"type": "init", "sessionId": self._params.session_id}

        # (ii) CR-A: resolve --mcp-servers (inline JSON OR spill to 0600 tmpfile).
        spill = await resolve_mcp_servers_flag(self._params.mcp_servers, self._params.session_id)
        self._mcp_spill_path = spill["spill_path"]

        # (iii) build argv (pure function — no I/O).
        argv = assemble_argv(
            session_id=self._params.session_id,
            prompt=prompt,
            protocol_version=self._params.protocol_version,
            resume=self._params.resume,
            cwd=self._params.cwd,
            provider_override=self._params.provider_override,
            mcp_servers_flag=spill["flag"],
            host_capabilities=self._params.host_capabilities,
            env_allowlist=self._params.env_allowlist,
            env_extra=self._params.env_extra,
            allow_protocol_skew=self._params.allow_protocol_skew,
        )

        # (iv) SC-B: spawn with start_new_session=True (posix setsid) so
        # PID == PGID and we can signal the whole group via os.killpg.
        try:
            proc = await asyncio.create_subprocess_exec(
                self._params.binary_path,
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._params.subprocess_env,
                cwd=self._params.cwd,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as err:
            # Spawn failure — synthesize a transport-class DisplayEvent.
            code = getattr(err, "errno", None)
            yield {
                "type": "error",
                "code": "spawn_failed",
                "classification": "transport",
                "severity": "error",
                "correlationId": "",
                "message": f"Failed to spawn engine subprocess ({code or 'unknown'}): {err}",
                "retryable": False,
            }
            await cleanup_spill_file(self._mcp_spill_path)
            self._mcp_spill_path = None
            return

        self._subprocess = proc

        # (v) accumulate stdout/stderr from the streams.
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _read_stream(stream: asyncio.StreamReader | None, buf: list[bytes]) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                buf.append(chunk)

        stdout_task = asyncio.create_task(_read_stream(proc.stdout, stdout_chunks))
        stderr_task = asyncio.create_task(_read_stream(proc.stderr, stderr_chunks))

        # Single-producer queue: activity ticks + the final event.
        queue: asyncio.Queue[DisplayEvent | None] = asyncio.Queue()
        finalized = False

        def finalize(ev: DisplayEvent) -> None:
            nonlocal finalized
            if finalized:
                return
            finalized = True
            queue.put_nowait(ev)
            queue.put_nowait(None)  # done sentinel

        # (vi) 2s activity ticker.
        tick_interval_s = _ACTIVITY_TICK_MS / 1000.0

        async def _ticker() -> None:
            try:
                while not finalized:
                    await asyncio.sleep(tick_interval_s)
                    if not finalized:
                        queue.put_nowait({"type": "activity"})
            except asyncio.CancelledError:
                return

        ticker_task = asyncio.create_task(_ticker())

        # (vii) race exit-wait vs timeout.
        timeout_s = self._params.timeout_ms / 1000.0

        async def _wait_for_exit() -> None:
            await proc.wait()
            # Drain stdio reader tasks before producing the terminal event.
            await stdout_task
            await stderr_task
            if finalized:
                return
            stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            ev = parse_run_output(
                {
                    "stdout": stdout,
                    "stderr": stderr,
                    "exitCode": proc.returncode if proc.returncode is not None else -1,
                }
            )
            finalize(ev)

        exit_task = asyncio.create_task(_wait_for_exit())

        async def _timeout_watch() -> None:
            try:
                await asyncio.sleep(timeout_s)
            except asyncio.CancelledError:
                return
            if finalized:
                return
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            tail = _stderr_tail_of(stderr)
            timeout_event: DisplayEvent = {
                "type": "error",
                "code": "engine_hung",
                "classification": "engine",
                "severity": "error",
                "correlationId": "",
                "message": (
                    f"Engine subprocess hung past {self._params.timeout_ms}ms "
                    f"timeout; SIGTERM/SIGKILL escalation invoked."
                ),
                "retryable": False,
            }
            if tail is not None:
                timeout_event["stderrTail"] = tail
            finalize(timeout_event)
            # Fire-and-forget: cancel races the next event-loop turn. Keep
            # a strong reference (RUF006) so the cancel task is not GC'd.
            cancel_task = asyncio.create_task(self.cancel())
            self._background_tasks.add(cancel_task)
            cancel_task.add_done_callback(self._background_tasks.discard)

        timeout_task = asyncio.create_task(_timeout_watch())

        # (ix) drain loop — yield activity events then the final event.
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            # (viii) cleanup ticker + tasks + spill file on every exit path.
            ticker_task.cancel()
            timeout_task.cancel()
            for t in (ticker_task, timeout_task, exit_task, stdout_task, stderr_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            await cleanup_spill_file(self._mcp_spill_path)
            self._mcp_spill_path = None

    async def cancel(self) -> None:
        """Cancel the running subprocess via SIGTERM-then-SIGKILL on the whole
        process group (SC-B), then unlink any MCP spill file (CR-A).

        Idempotent: safe to call when the subprocess has already exited and
        safe to call when no spill file was created. ``ProcessLookupError``
        is swallowed (process group already gone).
        """
        proc = self._subprocess
        if proc is not None and proc.returncode is None and proc.pid is not None:
            pid = proc.pid
            try:
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                pgid = pid

            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            # Grace window between SIGTERM and SIGKILL.
            grace_s = _SIGKILL_GRACE_MS / 1000.0
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace_s)
            except TimeoutError:
                # Engine did not exit within grace; escalate to SIGKILL.
                if proc.returncode is None:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        if self._mcp_spill_path is not None:
            path = self._mcp_spill_path
            self._mcp_spill_path = None
            await cleanup_spill_file(path)

    async def dispose(self) -> None:
        """Graceful shutdown — alias for ``cancel()`` (D3)."""
        await self.cancel()
