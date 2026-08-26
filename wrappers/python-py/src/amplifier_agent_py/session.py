"""SessionHandle — subprocess driver for Mode A v2.

Mirrors wrappers/typescript/src/session.ts.

Per the 2026-05-24 Mode A pivot amendment §5.2: each ``submit()`` call spawns
a fresh ``amplifier-agent run`` subprocess with the assembled argv.  The async
iterable yields:

  1. ``InitEvent(session_id)`` — yielded SYNCHRONOUSLY before the subprocess
     is spawned (SC-1: no race window with the activity ticker).
  2. ``ActivityEvent()`` — yielded every 2 seconds while the subprocess is alive
     (preserves NC's stuck-detection signal without engine-side cooperation).
  3. ``ResultEvent(text)`` or ``ErrorEvent(...)`` — yielded once when the
     subprocess exits (``parse_run_output`` applied to stdout/stderr/exit) OR
     when the configured ``timeout_ms`` (if > 0) elapses (synthesized
     ``engine_hung``).  No timeout is armed when ``timeout_ms`` is None or 0.
  4. ``NotificationEvent(method, params)`` — yielded for each wire-protocol
     notification parsed from the engine's stderr NDJSON stream.

Lifecycle:
  - ``submit()`` is one-shot per session (D10). A second call raises
    ``AaaError(lifecycle_unsupported)``.
  - ``cancel()`` SIGTERMs the whole process group (SC-B), waits up to 5s, then
    SIGKILLs if the engine has not exited.  It also unlinks any MCP spill
    file created on this turn (CR-A cleanup).
  - ``dispose()`` is a synonym for ``cancel()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .argv_builder import ApprovalMode, AssembleArgvInput, DisplayMode, assemble_argv
from .errors import AaaError
from .mcp_spill import cleanup_spill_file, resolve_mcp_config_path
from .prompt_spill import cleanup_prompt_spill_file, resolve_prompt_file_path
from .run_output_parser import STDERR_TAIL_BYTES, SubprocessOutcome, parse_run_output, tail_stderr_bytes
from .types import (
    ActivityEvent,
    DisplayEvent,
    EngineInfo,
    ErrorEvent,
    InitEvent,
    McpServerConfig,
    NotificationEvent,
)

#: Default timeout exported for callers that want a wall-clock cap on
#: individual turns (10 minutes).  NOT applied automatically — pass
#: ``timeout_ms=DEFAULT_TIMEOUT_MS`` to opt in.
DEFAULT_TIMEOUT_MS = 10 * 60 * 1000

#: Activity ticker interval: 2 seconds (NC stuck-detection has 10s threshold).
_ACTIVITY_TICK_S = 2.0

#: Grace window between SIGTERM and SIGKILL in ``cancel()``.
_SIGKILL_GRACE_S = 5.0


@dataclass(kw_only=True)
class SessionHandleParams:
    """Parameters for constructing a ``SessionHandle``.

    All session config is captured up-front and stored as instance state.
    The subprocess is not spawned until ``submit()`` is called.
    """

    binary_path: str
    session_id: str
    subprocess_env: dict[str, str]
    protocol_version: str
    resume: bool = False
    cwd: str | None = None
    mcp_servers: dict[str, McpServerConfig | dict[str, Any]] | None = None
    config_path: str | None = None
    approval_mode: ApprovalMode | None = None
    display_mode: DisplayMode | None = None
    workspace: str | None = None
    timeout_ms: int | None = None
    display_on_event: Callable[[DisplayEvent], None] | None = None
    engine_version: str = ""
    bundle_digest: str = ""
    #: Byte cap on ``stderr_tail`` for BOTH the terminal ``ResultEvent`` and
    #: ``ErrorEvent``.  Positive int -> last N UTF-8 BYTES; ``None`` -> the
    #: entire buffer; ``0`` -> capture disabled.  The 4096 default preserves the
    #: historical ``ErrorEvent`` behaviour exactly.
    stderr_tail_bytes: int | None = STDERR_TAIL_BYTES


@dataclass
class _QueueState:
    """Internal queue + finalization state shared with the iterator."""

    queue: asyncio.Queue[DisplayEvent | None] = field(default_factory=asyncio.Queue)
    finalized: bool = False


class SessionHandle:
    """One-shot session handle that drives the engine subprocess.

    Mirrors ``SessionHandle`` in TS session.ts.  ``submit()`` is one-shot per
    session; ``cancel()`` and ``dispose()`` are idempotent.
    """

    def __init__(self, params: SessionHandleParams) -> None:
        self._params = params
        self._submitted = False
        self._subprocess: asyncio.subprocess.Process | None = None
        self._mcp_spill_path: str | None = None
        self._prompt_spill_path: str | None = None
        self._timeout_cancel_task: asyncio.Task[None] | None = None
        self._engine_info = EngineInfo(
            binary_path=params.binary_path,
            protocol_version=params.protocol_version,
            engine_version=params.engine_version,
            bundle_digest=params.bundle_digest,
        )

    def get_engine_info(self) -> EngineInfo:
        """Return resolved engine metadata (D5)."""
        return self._engine_info

    def submit(self, prompt: str) -> AsyncIterator[DisplayEvent]:
        """Submit a prompt and return an async iterator of ``DisplayEvent``.

        One-shot per session (D10): raises ``AaaError`` on second call.
        """
        if self._submitted:
            raise AaaError(
                "lifecycle_unsupported",
                "SessionHandle.submit() is one-shot per session (D10); already submitted",
            )
        self._submitted = True
        return self._make_iterable(prompt)

    async def _make_iterable(self, prompt: str) -> AsyncIterator[DisplayEvent]:
        """Async generator implementing the §5.2 iterable behavior."""
        # (i) SC-1: yield init synchronously, BEFORE any async work.
        yield InitEvent(session_id=self._params.session_id)

        # (ii) CR-A: spill MCP servers to a 0600 tmpfile (configPath is None
        #      when mcp_servers is None/empty).
        spill = resolve_mcp_config_path(self._params.mcp_servers, self._params.session_id)
        self._mcp_spill_path = spill.config_path

        # (ii-b) Same treatment for an oversized prompt: spill it to a 0600
        #        tmpfile so it never has to fit through argv, which is capped at
        #        131072 bytes per element on Linux and 32767 chars for the whole
        #        command line on Windows.  prompt_file is None when the prompt is
        #        small enough to travel positionally.  The file is fully written
        #        and closed here, BEFORE the spawn below, because on Windows a
        #        child cannot open a file the parent still holds.
        prompt_spill = resolve_prompt_file_path(prompt, self._params.session_id)
        self._prompt_spill_path = prompt_spill.prompt_file

        # (iii) Build argv (pure function — no I/O).
        argv = assemble_argv(
            AssembleArgvInput(
                session_id=self._params.session_id,
                prompt=prompt,
                prompt_file=prompt_spill.prompt_file,
                protocol_version=self._params.protocol_version,
                resume=self._params.resume,
                cwd=self._params.cwd,
                config_path=self._params.config_path,
                approval_mode=self._params.approval_mode,
                display_mode=self._params.display_mode,
                workspace=self._params.workspace,
            )
        )

        # Build subprocess env, injecting AMPLIFIER_MCP_CONFIG when we spilled.
        subprocess_env = dict(self._params.subprocess_env)
        if spill.config_path is not None:
            subprocess_env["AMPLIFIER_MCP_CONFIG"] = spill.config_path

        # Single-producer queue: activity ticks + notification events + the
        # terminal event.  ``None`` sentinel marks completion.
        state = _QueueState()
        display_on_event = self._params.display_on_event

        def finalize(ev: DisplayEvent) -> None:
            """Push the terminal event and the completion sentinel exactly once."""
            if state.finalized:
                return
            state.finalized = True
            state.queue.put_nowait(ev)
            state.queue.put_nowait(None)

        # (iv) SC-B: spawn with start_new_session=True → new session group →
        #      PID == PGID for group signals.  Mirrors detached:true on POSIX.
        try:
            proc = await asyncio.create_subprocess_exec(
                self._params.binary_path,
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=subprocess_env,
                cwd=self._params.cwd,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            # Spawn-time failure (ENOENT, EACCES) → typed error.  No engine ran,
            # so there is no turn id to report -- but the session id is the
            # handle's own, and a host correlating this failure needs it.
            tail = None
            yield ErrorEvent(
                code="spawn_failed",
                classification="transport",
                severity="error",
                correlation_id="",
                message=f"Failed to spawn engine subprocess ({type(e).__name__}): {e}",
                stderr_tail=tail,
                retryable=False,
                session_id=self._params.session_id,
            )
            cleanup_spill_file(self._mcp_spill_path)
            self._mcp_spill_path = None
            cleanup_prompt_spill_file(self._prompt_spill_path)
            self._prompt_spill_path = None
            return

        self._subprocess = proc

        # Buffers for stdout/stderr accumulation.
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []

        async def drain_stdout() -> None:
            """Accumulate stdout chunks until EOF."""
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    return
                stdout_buf.append(chunk.decode(errors="replace"))

        async def drain_stderr() -> None:
            """Parse NDJSON from stderr line-by-line.

            JSON lines become ``NotificationEvent`` and feed both the iterator
            queue and the optional ``display.on_event`` callback.  Non-JSON
            lines are accumulated into ``stderr_buf`` so the stderr tail
            surfaces in diagnostic snapshots.  JSON lines are also recorded
            in ``stderr_buf`` verbatim so a crash-time tail keeps wire-event
            context.
            """
            assert proc.stderr is not None
            from .transport import parse_ndjson_stream  # local to avoid cycle

            def on_json(obj: dict[str, Any]) -> None:
                stderr_buf.append(json.dumps(obj) + "\n")
                method = obj.get("method")
                method_str = method if isinstance(method, str) else "unknown"
                params = obj.get("params") if "params" in obj else obj
                ev = NotificationEvent(method=method_str, params=params)
                if not state.finalized:
                    state.queue.put_nowait(ev)
                if display_on_event is not None:
                    try:
                        display_on_event(ev)
                    except Exception:
                        # Host callback errors must not poison the stream.
                        pass

            def on_non_json(line: str) -> None:
                stderr_buf.append(line + "\n")

            await parse_ndjson_stream(proc.stderr, on_json=on_json, on_non_json=on_non_json)

        async def activity_ticker() -> None:
            """Emit ``ActivityEvent`` every 2s until finalized."""
            while not state.finalized:
                try:
                    await asyncio.sleep(_ACTIVITY_TICK_S)
                except asyncio.CancelledError:
                    return
                if not state.finalized:
                    state.queue.put_nowait(ActivityEvent())

        async def watch_exit() -> None:
            """Await subprocess exit and push the terminal event."""
            exit_code = await proc.wait()
            if state.finalized:
                return
            ev = parse_run_output(
                SubprocessOutcome(
                    stdout="".join(stdout_buf),
                    stderr="".join(stderr_buf),
                    exit_code=exit_code,
                ),
                stderr_tail_bytes=self._params.stderr_tail_bytes,
                # Rule 2 synthesis has no envelope to read identity from; hand
                # the parser the session id we already hold so the synthesized
                # error still carries it.  Ignored on the Rule 1 path, where the
                # envelope is authoritative.
                fallback_session_id=self._params.session_id,
            )
            finalize(ev)

        async def watch_timeout() -> None:
            """Synthesize ``engine_hung`` if ``timeout_ms`` elapses first."""
            timeout_ms = self._params.timeout_ms
            if timeout_ms is None or timeout_ms <= 0:
                return
            try:
                await asyncio.sleep(timeout_ms / 1000.0)
            except asyncio.CancelledError:
                return
            if state.finalized:
                return
            tail = tail_stderr_bytes("".join(stderr_buf), self._params.stderr_tail_bytes)
            finalize(
                ErrorEvent(
                    code="engine_hung",
                    classification="engine",
                    severity="error",
                    correlation_id="",
                    message=(
                        f"Engine subprocess hung past {timeout_ms}ms timeout; SIGTERM/SIGKILL escalation invoked."
                    ),
                    stderr_tail=tail,
                    retryable=False,
                    # The engine never reported a turn id, but the session id is
                    # ours -- report it so a hung turn is still correlatable.
                    session_id=self._params.session_id,
                )
            )
            # Fire-and-forget cancel; keep a reference so the task is not
            # garbage-collected before it runs (RUF006).
            self._timeout_cancel_task = asyncio.create_task(self.cancel())

        # Spawn background tasks.
        tasks = [
            asyncio.create_task(drain_stdout()),
            asyncio.create_task(drain_stderr()),
            asyncio.create_task(activity_ticker()),
            asyncio.create_task(watch_exit()),
            asyncio.create_task(watch_timeout()),
        ]

        try:
            # (v) Drain loop — yield events until the completion sentinel.
            while True:
                item = await state.queue.get()
                if item is None:
                    return
                yield item
        finally:
            # (vi) Cleanup: cancel background tasks, drain stdout/stderr,
            #      unlink spill file.  Each step is best-effort.
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            cleanup_spill_file(self._mcp_spill_path)
            self._mcp_spill_path = None
            cleanup_prompt_spill_file(self._prompt_spill_path)
            self._prompt_spill_path = None

    async def cancel(self) -> None:
        """Cancel the running subprocess via SIGTERM-then-SIGKILL.

        Targets the whole process group (SC-B) so MCP child processes the
        engine launched receive the same signal.  Then unlinks any MCP spill
        file (CR-A).

        Idempotent: safe to call when the subprocess has already exited and
        safe to call when no spill file was created.  Errors from
        ``os.killpg`` are swallowed (``ProcessLookupError`` means the group
        is already gone).
        """
        proc = self._subprocess
        if proc is not None and proc.returncode is None and proc.pid is not None:
            pgid = proc.pid
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_SIGKILL_GRACE_S)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(pgid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await proc.wait()
        if self._mcp_spill_path is not None:
            path = self._mcp_spill_path
            self._mcp_spill_path = None
            cleanup_spill_file(path)
        if self._prompt_spill_path is not None:
            prompt_path = self._prompt_spill_path
            self._prompt_spill_path = None
            cleanup_prompt_spill_file(prompt_path)

    async def dispose(self) -> None:
        """Graceful shutdown — alias for ``cancel()`` (D3)."""
        await self.cancel()
