"""MCP servers config path resolution.

Mirrors wrappers/typescript/src/mcp-spill.ts.

The wrapper always spills the MCP server map to a 0600 tmpfile under
``${XDG_RUNTIME_DIR || tempfile.gettempdir()}/amplifier-agent/<session_id>/mcp.json``.
The file is written in the format documented by ``amplifier-module-tool-mcp``:
a top-level ``{"mcpServers": <map>}`` object.  The caller (``SessionHandle``)
injects the spilled path into the engine's subprocess environment as
``AMPLIFIER_MCP_CONFIG``; the module reads it via its standard config
discovery (config.py priority chain).  The former ``--mcp-config-path`` argv
flag was dropped — the env var is the single forwarding mechanism.

``cleanup_spill_file()`` is the matching teardown — idempotent unlink that
swallows ``FileNotFoundError`` so callers can call it unconditionally on every
exit path.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import McpServerConfig, mcp_server_to_dict


@dataclass(frozen=True, kw_only=True)
class McpSpillResult:
    """Result of spilling the MCP servers map to a tmpfile.

    When ``mcp_servers`` is None/empty: ``config_path`` is ``None``.
    Otherwise: ``config_path`` points at the 0600 spill file containing
    ``{"mcpServers": <map>}``.
    """

    config_path: str | None


def _spill_base_dir() -> Path:
    """Compute the base directory for spill files.

    Prefers ``$XDG_RUNTIME_DIR/amplifier-agent`` (typically a tmpfs on Linux)
    and falls back to ``tempfile.gettempdir()/amplifier-agent`` otherwise.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "amplifier-agent"
    return Path(tempfile.gettempdir()) / "amplifier-agent"


def resolve_mcp_config_path(
    mcp_servers: dict[str, McpServerConfig | dict[str, Any]] | None,
    session_id: str,
) -> McpSpillResult:
    """Resolve the MCP config file path for ``AMPLIFIER_MCP_CONFIG``.

    Always spills to a 0600 tmpfile.  The file content wraps the server map
    in the top-level ``mcpServers`` key that ``amplifier-module-tool-mcp``
    expects.

    Args:
        mcp_servers: Map of server-id -> config (``McpServerConfig`` or raw
                     dict), or ``None``.
        session_id:  Session identifier; used as the per-session subdirectory
                     under the spill base so concurrent sessions never clash.

    Returns:
        ``McpSpillResult`` with the on-disk config path (or ``None`` if there
        are no servers to spill).
    """
    if not mcp_servers:
        return McpSpillResult(config_path=None)

    serialized: dict[str, dict[str, Any]] = {sid: mcp_server_to_dict(cfg) for sid, cfg in mcp_servers.items()}

    base = _spill_base_dir()
    session_dir = base / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    # Tighten the per-session directory to 0700.
    try:
        session_dir.chmod(0o700)
    except PermissionError:
        # Best effort; if we cannot tighten perms, still proceed with the file write.
        pass

    file_path = session_dir / "mcp.json"
    payload = json.dumps({"mcpServers": serialized})

    # Write with restrictive perms.  We write to the final path with 0600
    # using os.open() so file contents are never world-readable.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(file_path), flags, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
    except Exception:
        # If write fails, ensure the partial file does not linger.
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        raise

    return McpSpillResult(config_path=str(file_path))


def cleanup_spill_file(config_path: str | None) -> None:
    """Idempotently remove a spill file.

    Safe to call with ``None`` (no-op) and safe to call when the file is
    already gone (``FileNotFoundError`` swallowed).  Other I/O errors
    propagate.
    """
    if not config_path:
        return
    try:
        os.unlink(config_path)
    except FileNotFoundError:
        return
