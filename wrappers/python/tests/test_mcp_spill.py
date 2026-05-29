"""Tests for mcp_spill.py: resolve_mcp_config_path() and cleanup_spill_file().

Mirror of wrappers/typescript/test/mcp-spill.test.ts.

Protocol 0.2.0 cases:
(i)   None/empty mcp_servers returns {"config_path": None}
(ii)  any non-empty server map -> always spilled to a 0600 tmpfile under a
      0700 per-session dir; file payload wraps the map in the top-level
      "mcpServers" key so amplifier-module-tool-mcp can load it via
      AMPLIFIER_MCP_CONFIG (engine sets that env from --mcp-config-path).
(iii) cleanup_spill_file is idempotent (FileNotFoundError swallowed)
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from amplifier_agent_client.mcp_spill import (
    cleanup_spill_file,
    resolve_mcp_config_path,
)

SID = "test-session-abc"


_created: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup_created_spill_files() -> Generator[None, None, None]:
    """Ensure every spill file created during a test is cleaned up after."""
    yield
    while _created:
        p = _created.pop()
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


@pytest.mark.asyncio
async def test_returns_none_config_path_for_none_mcp_servers() -> None:
    """(i) returns {config_path: None} for None mcp_servers."""
    result = await resolve_mcp_config_path(None, SID)
    assert result == {"config_path": None}


@pytest.mark.asyncio
async def test_returns_none_config_path_for_empty_dict() -> None:
    """(i) returns {config_path: None} for empty mcp_servers dict."""
    result = await resolve_mcp_config_path({}, SID)
    assert result == {"config_path": None}


@pytest.mark.asyncio
async def test_spills_to_tmpfile_with_0600_mode_when_servers_present() -> None:
    """(ii) any non-empty server map is spilled to a 0600 tmpfile with the
    top-level ``{"mcpServers": ...}`` wrapper that amplifier-module-tool-mcp
    expects when reading ``AMPLIFIER_MCP_CONFIG``.
    """
    mcp_servers = {
        "alpha": {"command": "echo", "args": ["hi"]},
        "secret": {
            "command": "run-secret",
            "env": {"API_KEY": "super-secret-value"},
        },
    }
    result = await resolve_mcp_config_path(mcp_servers, SID)
    config_path = result["config_path"]
    assert config_path is not None
    _created.append(config_path)

    # Path must be a bare filesystem path (no leading '@' marker — that
    # convention belonged to the 0.1.0 wire shape).
    assert not config_path.startswith("@")

    # File contents must wrap the map under top-level "mcpServers" so the
    # tool-mcp module can ingest it via AMPLIFIER_MCP_CONFIG.
    contents = Path(config_path).read_text("utf-8")
    assert json.loads(contents) == {"mcpServers": mcp_servers}

    # File mode should be 0600 (owner read/write only).
    st = os.stat(config_path)
    mode = st.st_mode & 0o777
    assert mode == 0o600


@pytest.mark.asyncio
async def test_spill_always_runs_even_without_env_blocks() -> None:
    """(ii) 0.2.0 always spills — there is no longer an inline-JSON branch."""
    mcp_servers = {
        "alpha": {"command": "echo", "args": ["hi"]},
        # env present but empty — still spilled in 0.2.0.
        "beta": {"command": "true", "env": {}},
    }
    result = await resolve_mcp_config_path(mcp_servers, SID)
    config_path = result["config_path"]
    assert config_path is not None
    _created.append(config_path)
    assert os.path.exists(config_path)
    contents = json.loads(Path(config_path).read_text("utf-8"))
    assert contents == {"mcpServers": mcp_servers}


@pytest.mark.asyncio
async def test_cleanup_spill_file_is_idempotent_on_missing_path() -> None:
    """(iii) cleanup_spill_file is idempotent — second call on missing file does not throw."""
    with tempfile.TemporaryDirectory(prefix="mcp-spill-cleanup-") as dir_:
        path = os.path.join(dir_, "mcp.json")
        Path(path).write_text("{}", "utf-8")
        os.chmod(path, 0o600)

        # First cleanup removes it
        await cleanup_spill_file(path)
        assert not os.path.exists(path)

        # Second cleanup on missing path must not throw (FileNotFoundError swallowed)
        await cleanup_spill_file(path)


@pytest.mark.asyncio
async def test_cleanup_spill_file_is_no_op_for_none_input() -> None:
    """(iii) cleanup_spill_file is a no-op for None input."""
    await cleanup_spill_file(None)
