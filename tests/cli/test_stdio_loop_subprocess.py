"""Subprocess integration tests for Mode B stdio transport (Task 9).

These tests spawn the actual CLI binary via asyncio.create_subprocess_exec
and exercise the full stdin/stdout wire over process pipes.

Test matrix (3 tests):
  1. test_subprocess_stdin_close_triggers_graceful_exit
     — close stdin immediately (EOF) → exit_code 0

  2. test_subprocess_rejects_non_initialize_first
     — send turn/submit before agent/initialize → agent_not_ready error
       response, close stdin → exit_code 0

  3. test_subprocess_handshake_and_shutdown
     — full lifecycle: agent/initialize, verify serverInfo.name, send
       agent/shutdown, verify response, wait for exit_code 0.
     Skipped when Engine.initialize cannot complete (Phase 4 dependency).

All tests run the subprocess from REPO_ROOT with PYTHONUNBUFFERED=1 and
kill the process in the finally-block if it is still alive.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_line(data: dict) -> bytes:  # type: ignore[type-arg]
    """Encode *data* as a compact NDJSON line (dict → JSON + newline)."""
    return json.dumps(data, separators=(",", ":")).encode("utf-8") + b"\n"


async def _read_one(
    stdout: asyncio.StreamReader,
    *,
    timeout: float = 5.0,
) -> dict:  # type: ignore[type-arg]
    """Read one NDJSON line from *stdout* and return it as a dict.

    Raises ``asyncio.TimeoutError`` if no line arrives within *timeout*
    seconds.
    """
    line = await asyncio.wait_for(stdout.readline(), timeout=timeout)
    return json.loads(line)


async def _read_until(
    stdout: asyncio.StreamReader,
    predicate: Any,
    *,
    timeout: float = 5.0,
) -> list[dict]:  # type: ignore[type-arg]
    """Read NDJSON lines until *predicate(msg)* is True or *timeout* expires.

    Returns all lines read (including the one that satisfied the predicate).
    On timeout the partial list is returned without raising.
    """
    messages: list[dict] = []  # type: ignore[type-arg]
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            line = await asyncio.wait_for(stdout.readline(), timeout=remaining)
        except TimeoutError:
            break
        if not line:
            break
        msg = json.loads(line)
        messages.append(msg)
        if predicate(msg):
            break
    return messages


def _spawn_env() -> dict[str, str]:
    """Return a copy of os.environ with PYTHONUNBUFFERED=1 set."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


async def _spawn_stdio_proc() -> asyncio.subprocess.Process:
    """Spawn `python -m amplifier_agent_cli run --stdio` as a subprocess."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "amplifier_agent_cli",
        "run",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
        env=_spawn_env(),
    )


# ---------------------------------------------------------------------------
# skipif helper: check whether Engine.initialize can complete in-process
# ---------------------------------------------------------------------------


def _engine_can_initialize() -> bool:
    """Return True if the Phase 1 Engine.boot() completes without error.

    This is a synchronous check run at collection time.  It creates an Engine
    with stub protocol points and calls boot() in a temporary event loop.
    Returns False only if the Engine itself raises (e.g. missing Phase 4 deps).
    """
    try:
        import asyncio as _asyncio

        from amplifier_agent_lib.engine import Engine

        async def _try() -> bool:
            async def _stub(ctx: Any) -> str:
                return ""

            engine = Engine(
                turn_handler=_stub,
                protocol_points={  # type: ignore[arg-type]
                    "approval": None,
                    "display": None,
                },
            )
            await engine.boot(
                {
                    "capabilities": {},
                    "sessionId": "",
                    "resume": False,
                }
            )
            return True

        return _asyncio.run(_try())
    except Exception:
        return False


_SKIP_HANDSHAKE = not _engine_can_initialize()
_SKIP_REASON = "Engine.initialize requires Phase 4 bundle availability"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subprocess_stdin_close_triggers_graceful_exit() -> None:
    """Closing stdin immediately sends EOF → the loop exits cleanly with 0.

    This exercises the very first code-path in the asyncio message loop:
    EOF on the reader → ``return 0`` without writing any output.
    No agent/initialize is required.
    """
    proc = await _spawn_stdio_proc()
    try:
        assert proc.stdin is not None
        proc.stdin.close()

        exit_code = await asyncio.wait_for(proc.wait(), timeout=10.0)
        assert exit_code == 0, f"Expected exit_code 0 on immediate stdin close, got {exit_code}"
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.asyncio
async def test_subprocess_rejects_non_initialize_first() -> None:
    """Sending turn/submit before agent/initialize returns agent_not_ready.

    Flow:
    1. Send ``turn/submit`` as the very first message.
    2. Read the JSON-RPC error response — must have data.code=``agent_not_ready``.
    3. Close stdin → loop exits with 0.

    This test does NOT rely on Engine.initialize; it verifies the guard path
    that rejects un-initialized requests, which is purely inside stdio_loop.
    """
    proc = await _spawn_stdio_proc()
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        # Send turn/submit FIRST (before initialize)
        msg: dict = {  # type: ignore[type-arg]
            "jsonrpc": "2.0",
            "id": 1,
            "method": "turn/submit",
            "params": {"sessionId": "s1", "turnId": "t1", "prompt": "hello"},
        }
        proc.stdin.write(_write_line(msg))
        await proc.stdin.drain()

        # Expect an error response
        response = await _read_one(proc.stdout, timeout=5.0)

        assert "error" in response, f"Expected JSON-RPC error response, got: {response}"
        error = response["error"]
        assert "data" in error, f"Expected 'data' key in error, got: {error}"
        assert error["data"]["code"] == "agent_not_ready", (
            f"Expected data.code=='agent_not_ready', got: {error['data']}"
        )

        # Close stdin → graceful exit
        proc.stdin.close()
        exit_code = await asyncio.wait_for(proc.wait(), timeout=10.0)
        assert exit_code == 0, f"Expected exit_code 0 after stdin close, got {exit_code}"
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.asyncio
@pytest.mark.skipif(_SKIP_HANDSHAKE, reason=_SKIP_REASON)
async def test_subprocess_handshake_and_shutdown() -> None:
    """Full Mode B session: initialize → verify serverInfo → shutdown → exit 0.

    Flow:
    1. Send ``agent/initialize`` with minimal capabilities.
    2. Read response — must have result.serverInfo.name == 'amplifier-agent'.
    3. Send ``agent/shutdown``.
    4. Read shutdown response — must have 'result' key.
    5. Wait for subprocess to exit with code 0.
    """
    proc = await _spawn_stdio_proc()
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        # --- Step 1: agent/initialize -----------------------------------------
        init_msg: dict = {  # type: ignore[type-arg]
            "jsonrpc": "2.0",
            "id": 1,
            "method": "agent/initialize",
            "params": {
                "capabilities": {
                    "approval": {"actions": ["accept", "decline", "cancel"]},
                    "display": {"events": []},
                },
                "clientInfo": {"name": "integration-test", "version": "0.0.1"},
            },
        }
        proc.stdin.write(_write_line(init_msg))
        await proc.stdin.drain()

        # --- Step 2: read initialize response ---------------------------------
        init_response = await _read_one(proc.stdout, timeout=10.0)

        assert "result" in init_response, f"Expected 'result' in initialize response, got: {init_response}"
        assert "error" not in init_response, f"Unexpected error in initialize response: {init_response}"
        result = init_response["result"]
        server_info = result.get("serverInfo", {})
        assert server_info.get("name") == "amplifier-agent", (
            f"Expected serverInfo.name=='amplifier-agent', got: {server_info}"
        )

        # --- Step 3: agent/shutdown -------------------------------------------
        shutdown_msg: dict = {  # type: ignore[type-arg]
            "jsonrpc": "2.0",
            "id": 2,
            "method": "agent/shutdown",
            "params": {},
        }
        proc.stdin.write(_write_line(shutdown_msg))
        await proc.stdin.drain()

        # --- Step 4: read shutdown response -----------------------------------
        shutdown_response = await _read_one(proc.stdout, timeout=5.0)

        assert "result" in shutdown_response, f"Expected 'result' in shutdown response, got: {shutdown_response}"
        assert "error" not in shutdown_response, f"Unexpected error in shutdown response: {shutdown_response}"

        # --- Step 5: wait for clean exit -------------------------------------
        exit_code = await asyncio.wait_for(proc.wait(), timeout=5.0)
        assert exit_code == 0, f"Expected exit_code 0 after shutdown, got {exit_code}"
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
