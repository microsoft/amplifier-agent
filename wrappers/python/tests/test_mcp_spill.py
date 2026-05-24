"""Tests for mcp_spill.py: resolve_mcp_servers_flag() and cleanup_spill_file().

Mirror of wrappers/typescript/test/mcp-spill.test.ts.

TDD cases (task-6 / A3'/CR-A):
(i)   null/empty mcp_servers returns (flag=None, spill_path=None)
(ii)  no server has non-empty env block -> inline JSON, no spill file
      (flag does not start with '@')
(iii) any server has non-empty env block -> spill to tmpfile, flag is
      `@<path>`, file contains full config, mode is 0600
(iv)  cleanup_spill_file is idempotent (FileNotFoundError swallowed)
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
    resolve_mcp_servers_flag,
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
async def test_returns_none_pair_for_none_mcp_servers() -> None:
    """(i) returns (None, None) for None mcp_servers."""
    result = await resolve_mcp_servers_flag(None, SID)
    assert result == {"flag": None, "spill_path": None}


@pytest.mark.asyncio
async def test_returns_none_pair_for_empty_dict() -> None:
    """(i) returns (None, None) for empty mcp_servers dict."""
    result = await resolve_mcp_servers_flag({}, SID)
    assert result == {"flag": None, "spill_path": None}


@pytest.mark.asyncio
async def test_inlines_json_when_no_server_has_non_empty_env_block() -> None:
    """(ii) inlines JSON when no server has a non-empty env block."""
    mcp_servers = {
        "alpha": {"command": "echo", "args": ["hi"]},
        # env present but empty -> still considered "no env" for spill purposes
        "beta": {"command": "true", "env": {}},
    }
    result = await resolve_mcp_servers_flag(mcp_servers, SID)
    assert result["spill_path"] is None
    assert result["flag"] is not None
    # Inline JSON: must NOT start with '@'
    assert not result["flag"].startswith("@")
    assert json.loads(result["flag"]) == mcp_servers


@pytest.mark.asyncio
async def test_spills_to_tmpfile_with_0600_mode_when_any_server_has_non_empty_env_block() -> None:
    """(iii) spills to tmpfile with 0600 mode when any server has a non-empty env block."""
    mcp_servers = {
        "alpha": {"command": "echo"},
        "secret": {
            "command": "run-secret",
            "env": {"API_KEY": "super-secret-value"},
        },
    }
    result = await resolve_mcp_servers_flag(mcp_servers, SID)
    assert result["spill_path"] is not None
    assert result["flag"] is not None
    _created.append(result["spill_path"])

    # Flag should be '@<path>'
    assert result["flag"] == f"@{result['spill_path']}"
    assert result["flag"].startswith("@")

    # File contents should be the full mcp_servers config
    contents = Path(result["spill_path"]).read_text("utf-8")
    assert json.loads(contents) == mcp_servers

    # File mode should be 0600 (owner read/write only)
    st = os.stat(result["spill_path"])
    mode = st.st_mode & 0o777
    assert mode == 0o600


@pytest.mark.asyncio
async def test_cleanup_spill_file_is_idempotent_on_missing_path() -> None:
    """(iv) cleanup_spill_file is idempotent — second call on missing file does not throw."""
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
    """(iv) cleanup_spill_file is a no-op for None input."""
    await cleanup_spill_file(None)
