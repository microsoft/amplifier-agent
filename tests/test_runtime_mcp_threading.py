"""Tests for A5 — MCP config path threading and host-capabilities storage in _runtime.py.

Protocol 0.2.0: ``handle_initialize`` and ``make_turn_handler`` no longer
accept an inline ``mcpServers`` dict.  Instead they receive a pre-written
file path (``mcpConfigPath`` / ``mcp_config_path``) and forward it to
``tool-mcp`` by setting ``os.environ["AMPLIFIER_MCP_CONFIG"]``.  The module
reads the file via its standard config discovery (config.py priority chain).
"""

from __future__ import annotations

import os
import tempfile
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mcp_config_file(servers: dict[str, Any]) -> str:
    """Write a tmp file in the format tool-mcp expects and return the path."""
    fd, path = tempfile.mkstemp(prefix="test-mcp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"mcpServers": servers}, f)
    return path


def _make_params(
    *,
    session_id: str = "sess-test-1",
    mcp_config_path: str | None = None,
    host_capabilities: dict[str, Any] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Build a minimal InitializeParams dict for testing."""
    params: dict[str, Any] = {
        "sessionId": session_id,
        "resume": resume,
        "protocolVersion": "0.2.0",
        "clientInfo": {"name": "test-harness", "version": "0.0.0"},
        "capabilities": {"display": {"events": ["result/final"]}},
        "host": {"capabilities": host_capabilities or {}},
    }
    if mcp_config_path is not None:
        params["mcpConfigPath"] = mcp_config_path
    return params


def _make_mock_bundle() -> tuple[MagicMock, MagicMock]:
    """Return (mock_bundle, mock_session)."""
    mock_session = MagicMock()
    mock_session.metadata = {}

    async def _create_session(**_kwargs: Any) -> MagicMock:
        return mock_session

    mock_bundle = MagicMock()
    mock_bundle.create_session = _create_session

    return mock_bundle, mock_session


@pytest.mark.asyncio
async def test_mcp_config_path_forwarded_to_env() -> None:
    """mcpConfigPath from params must be set in AMPLIFIER_MCP_CONFIG env var."""
    from amplifier_agent_lib._runtime import handle_initialize

    mcp_servers = {"test-mcp": {"transport": "stdio", "command": "/usr/bin/echo", "args": ["hello"]}}
    config_path = _make_mcp_config_file(mcp_servers)

    try:
        params = _make_params(mcp_config_path=config_path)
        mock_bundle, _ = _make_mock_bundle()

        with (
            patch(
                "amplifier_agent_lib._runtime.load_and_prepare_cached",
                AsyncMock(return_value=mock_bundle),
            ),
        ):
            # Remove any pre-existing env var to ensure clean state.
            old_val = os.environ.pop("AMPLIFIER_MCP_CONFIG", None)
            try:
                await handle_initialize(params)
                assert os.environ.get("AMPLIFIER_MCP_CONFIG") == config_path, (
                    f"AMPLIFIER_MCP_CONFIG should be {config_path!r}, "
                    f"got {os.environ.get('AMPLIFIER_MCP_CONFIG')!r}"
                )
            finally:
                if old_val is None:
                    os.environ.pop("AMPLIFIER_MCP_CONFIG", None)
                else:
                    os.environ["AMPLIFIER_MCP_CONFIG"] = old_val
    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_missing_mcp_config_path_leaves_env_unchanged() -> None:
    """When mcpConfigPath is absent, AMPLIFIER_MCP_CONFIG is not set."""
    from amplifier_agent_lib._runtime import handle_initialize

    params = _make_params()  # no mcpConfigPath
    mock_bundle, _ = _make_mock_bundle()

    with (
        patch(
            "amplifier_agent_lib._runtime.load_and_prepare_cached",
            AsyncMock(return_value=mock_bundle),
        ),
    ):
        old_val = os.environ.pop("AMPLIFIER_MCP_CONFIG", None)
        try:
            await handle_initialize(params)
            assert "AMPLIFIER_MCP_CONFIG" not in os.environ, (
                "AMPLIFIER_MCP_CONFIG must not be set when mcpConfigPath is absent"
            )
        finally:
            if old_val is not None:
                os.environ["AMPLIFIER_MCP_CONFIG"] = old_val


@pytest.mark.asyncio
async def test_host_capabilities_stored_in_session_metadata() -> None:
    """host.capabilities from params must be stored in session.metadata['host_capabilities']."""
    from amplifier_agent_lib._runtime import handle_initialize

    host_caps = {"supports_structured_errors": True, "supports_steering": False}
    params = _make_params(host_capabilities=host_caps)
    mock_bundle, mock_session = _make_mock_bundle()

    with (
        patch(
            "amplifier_agent_lib._runtime.load_and_prepare_cached",
            AsyncMock(return_value=mock_bundle),
        ),
    ):
        await handle_initialize(params)

    assert mock_session.metadata.get("host_capabilities") == host_caps, (
        f"session.metadata['host_capabilities'] should be {host_caps!r}, "
        f"got {mock_session.metadata.get('host_capabilities')!r}"
    )


# ---------------------------------------------------------------------------
# make_turn_handler — CLI path mirror of handle_initialize's wire path.
# ---------------------------------------------------------------------------


def _make_mock_bundle_for_turn() -> tuple[MagicMock, MagicMock]:
    """Bundle mock for make_turn_handler tests."""
    mock_session = MagicMock()
    mock_session.metadata = {}
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.hooks = MagicMock()
    mock_session.coordinator.get = MagicMock(return_value=None)
    mock_session.execute = AsyncMock(return_value="ok")
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    async def _create_session(**_kwargs: Any) -> MagicMock:
        return mock_session

    mock_bundle = MagicMock()
    mock_bundle.mount_plan = {"agents": {}}
    mock_bundle.create_session = _create_session
    return mock_bundle, mock_session


@pytest.mark.asyncio
async def test_make_turn_handler_mcp_config_path_sets_env() -> None:
    """mcp_config_path kwarg must set AMPLIFIER_MCP_CONFIG — CLI path."""
    from amplifier_agent_lib._runtime import make_turn_handler

    mcp_servers = {"test-mcp": {"transport": "stdio", "command": "/usr/bin/echo", "args": ["hi"]}}
    config_path = _make_mcp_config_file(mcp_servers)

    try:
        mock_bundle, _ = _make_mock_bundle_for_turn()

        with patch("amplifier_agent_lib._runtime.SessionStore"), patch(
            "amplifier_agent_lib.bundle.hook_streaming.mount", AsyncMock(return_value=None)
        ):
            old_val = os.environ.pop("AMPLIFIER_MCP_CONFIG", None)
            try:
                handler = make_turn_handler(
                    mock_bundle,
                    cwd=None,
                    is_resumed=False,
                    mcp_config_path=config_path,
                )
                # Env var should be set immediately (at handler-creation time,
                # before the handler coroutine is awaited).
                assert os.environ.get("AMPLIFIER_MCP_CONFIG") == config_path, (
                    f"AMPLIFIER_MCP_CONFIG should be {config_path!r}, "
                    f"got {os.environ.get('AMPLIFIER_MCP_CONFIG')!r}"
                )
                ctx = MagicMock()
                ctx.session_id = "sess-test-1"
                ctx.turn_id = "turn-1"
                ctx.prompt = "ping"
                ctx.display = MagicMock()
                ctx.display.emit = AsyncMock()
                ctx.approval = MagicMock()
                ctx.approval.request = AsyncMock()
                await handler(ctx)
            finally:
                if old_val is None:
                    os.environ.pop("AMPLIFIER_MCP_CONFIG", None)
                else:
                    os.environ["AMPLIFIER_MCP_CONFIG"] = old_val
    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_make_turn_handler_no_mcp_config_path_leaves_env_unchanged() -> None:
    """When mcp_config_path is None, AMPLIFIER_MCP_CONFIG is not set."""
    from amplifier_agent_lib._runtime import make_turn_handler

    mock_bundle, _ = _make_mock_bundle_for_turn()

    with patch("amplifier_agent_lib._runtime.SessionStore"), patch(
        "amplifier_agent_lib.bundle.hook_streaming.mount", AsyncMock(return_value=None)
    ):
        old_val = os.environ.pop("AMPLIFIER_MCP_CONFIG", None)
        try:
            handler = make_turn_handler(mock_bundle, cwd=None, is_resumed=False)
            assert "AMPLIFIER_MCP_CONFIG" not in os.environ
            ctx = MagicMock()
            ctx.session_id = "sess-test-3"
            ctx.turn_id = "turn-1"
            ctx.prompt = "ping"
            ctx.display = MagicMock()
            ctx.display.emit = AsyncMock()
            ctx.approval = MagicMock()
            ctx.approval.request = AsyncMock()
            await handler(ctx)
        finally:
            if old_val is not None:
                os.environ["AMPLIFIER_MCP_CONFIG"] = old_val
