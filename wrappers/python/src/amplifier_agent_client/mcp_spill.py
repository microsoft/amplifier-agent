"""mcp_spill.py — MCP servers config path resolution (CR-A, protocol 0.2.0).

The wrapper always spills the MCP server map to a 0600 tmpfile under
``${XDG_RUNTIME_DIR or tempfile.gettempdir()}/amplifier-agent/<session_id>/mcp.json``.
The file is written in the format documented by amplifier-module-tool-mcp:
a top-level ``{"mcpServers": <map>}`` object. The caller (``SessionHandle``)
injects the spilled path into the engine's subprocess environment as
``AMPLIFIER_MCP_CONFIG``; the module reads it via its standard config
discovery (config.py priority chain). The former ``--mcp-config-path`` argv
flag was dropped — the env var is the single forwarding mechanism.

``cleanup_spill_file`` is the matching teardown — idempotent unlink that
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
    """Result of resolving the ``--mcp-config-path`` flag value.

    - When ``mcp_servers`` is None/empty: ``config_path`` is None.
    - When servers are present: ``config_path`` points at the 0600 spill file.
      The file contains ``{"mcpServers": <map>}`` in the format that
      amplifier-module-tool-mcp expects when reading ``AMPLIFIER_MCP_CONFIG``.
    """

    config_path: str | None


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


async def resolve_mcp_config_path(
    mcp_servers: dict[str, dict[str, Any]] | None,
    session_id: str,
) -> McpSpillResult:
    """Resolve the value to pass for ``--mcp-config-path``.

    Always spills the server map to a 0600 tmpfile (protocol 0.2.0: there is
    no longer an inline-JSON form on the command line). The on-disk payload
    wraps the map in the top-level ``mcpServers`` key that
    amplifier-module-tool-mcp expects when reading ``AMPLIFIER_MCP_CONFIG``.

    Args:
        mcp_servers: Map of server-id -> config, or None.
        session_id:  Used as per-session subdirectory under the spill base
                     so concurrent sessions never clash.

    Returns:
        ``McpSpillResult`` with the on-disk config path, or ``config_path``
        of ``None`` when there are no servers to spill.
    """
    if not mcp_servers:
        return {"config_path": None}

    # Always spill to a 0600 tmpfile under a 0700 per-session dir. Wrap the
    # server map in the top-level "mcpServers" key that the module expects
    # when reading AMPLIFIER_MCP_CONFIG (see tool-mcp/config.py).
    dir_path = os.path.join(_spill_base_dir(), session_id)
    file_path = os.path.join(dir_path, "mcp.json")
    payload = json.dumps({"mcpServers": mcp_servers})
    # asyncio.to_thread offloads blocking file I/O to the default executor.
    await asyncio.to_thread(_write_spill_file_sync, dir_path, file_path, payload)

    return {"config_path": file_path}


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
