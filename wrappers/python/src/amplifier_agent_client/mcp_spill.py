"""mcp_spill.py — secret-aware MCP servers config resolution (CR-A).

A3'/CR-A: When forwarding `--mcp-servers` to the engine binary, the wrapper
must avoid placing secret-bearing env blocks on the command line. If any
server in the config has a non-empty `env` block, the full JSON is spilled
to a 0600 tmpfile under `${XDG_RUNTIME_DIR or tempfile.gettempdir()}/amplifier-agent/<session_id>/`
and the flag value is `@<path>`. When no server has env, the JSON is inlined
directly (no spill, no cleanup needed).

`cleanup_spill_file` is the matching teardown — idempotent unlink that
swallows FileNotFoundError so callers can call it unconditionally on every
exit path.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, TypedDict


class McpSpillResult(TypedDict):
    """Result of resolving the `--mcp-servers` flag value.

    - When mcp_servers is None/empty: both fields are None.
    - When no server has a non-empty env block: ``flag`` is inline JSON,
      ``spill_path`` is None (no cleanup needed).
    - When any server has a non-empty env block: ``flag`` is ``@<spill_path>``,
      ``spill_path`` points at the 0600 tmpfile (caller must cleanup).
    """

    flag: str | None
    spill_path: str | None


def _any_server_has_env(mcp_servers: dict[str, dict[str, Any]]) -> bool:
    """Return True when at least one server has a non-empty `env` block.

    An empty dict ({}) does NOT trigger spilling — only env blocks with at
    least one key are considered secret-bearing.
    """
    for server in mcp_servers.values():
        if not isinstance(server, dict):
            continue
        env = server.get("env")
        if isinstance(env, dict) and len(env) > 0:
            return True
    return False


def _spill_base_dir() -> str:
    """Compute the base directory for spill files.

    Prefers ``$XDG_RUNTIME_DIR/amplifier-agent`` (typically tmpfs on Linux)
    and falls back to ``tempfile.gettempdir()/amplifier-agent`` otherwise.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return os.path.join(xdg, "amplifier-agent")
    return os.path.join(tempfile.gettempdir(), "amplifier-agent")


def _write_spill_file_sync(dir_path: str, file_path: str, payload: str) -> None:
    """Synchronously create the 0700 dir and write the 0600 spill file.

    Uses os.open + O_CREAT|O_WRONLY|O_TRUNC with mode 0o600 so that the
    file's permissions are restrictive even on a umask-022 host.
    """
    os.makedirs(dir_path, mode=0o700, exist_ok=True)
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    # Belt-and-suspenders: ensure mode is 0o600 even if the file pre-existed.
    os.chmod(file_path, 0o600)


async def resolve_mcp_servers_flag(
    mcp_servers: dict[str, dict[str, Any]] | None,
    session_id: str,
) -> McpSpillResult:
    """Resolve the value to pass for `--mcp-servers`.

    Args:
        mcp_servers: Map of server-id -> config, or None.
        session_id:  Used as per-session subdirectory under the spill base
                     so concurrent sessions never clash.

    Returns:
        ``McpSpillResult`` with the flag value and (if spilled) the on-disk
        path for later cleanup.
    """
    if not mcp_servers:
        return {"flag": None, "spill_path": None}

    if not _any_server_has_env(mcp_servers):
        # No secrets — safe to inline as a JSON string.
        return {"flag": json.dumps(mcp_servers), "spill_path": None}

    # Secret-bearing: spill to a 0600 tmpfile under a 0700 per-session dir.
    dir_path = os.path.join(_spill_base_dir(), session_id)
    file_path = os.path.join(dir_path, "mcp.json")
    payload = json.dumps(mcp_servers)
    # asyncio.to_thread offloads blocking file I/O to the default executor.
    await asyncio.to_thread(_write_spill_file_sync, dir_path, file_path, payload)

    return {"flag": f"@{file_path}", "spill_path": file_path}


async def cleanup_spill_file(spill_path: str | None) -> None:
    """Idempotently remove a spill file.

    Safe to call with ``None`` (no-op) and safe to call when the file is
    already gone (``FileNotFoundError`` swallowed). Other I/O errors
    propagate.
    """
    if not spill_path:
        return
    try:
        await asyncio.to_thread(os.unlink, spill_path)
    except FileNotFoundError:
        return
