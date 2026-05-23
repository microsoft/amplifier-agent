"""Tests for A5 — MCP threading and host-capabilities storage in _runtime.py.

Phase 2 A5 adds an ``handle_initialize(params)`` entry point to ``_runtime.py``
that loads the prepared bundle, threads wire-supplied ``mcpServers`` into
``tool-mcp.mount()`` via ``tool_overrides``, and stores ``host.capabilities``
on ``session.metadata`` for future capability-flag logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_params(
    *,
    session_id: str = "sess-test-1",
    mcp_servers: dict[str, Any] | None = None,
    host_capabilities: dict[str, Any] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Build a minimal InitializeParams dict for testing."""
    return {
        "sessionId": session_id,
        "resume": resume,
        "protocolVersion": "0.1.0",
        "clientInfo": {"name": "test-harness", "version": "0.0.0"},
        "capabilities": {"display": {"events": ["result/final"]}},
        "mcpServers": mcp_servers or {},
        "host": {"capabilities": host_capabilities or {}},
    }


def _make_mock_bundle(
    *,
    tool_mcp_static_config: dict[str, Any] | None = None,
) -> tuple[MagicMock, dict[str, Any], MagicMock]:
    """Return (mock_bundle, captured_create_session_kwargs, mock_session)."""
    captured: dict[str, Any] = {}

    mock_session = MagicMock()
    mock_session.metadata = {}

    async def _create_session(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return mock_session

    mock_bundle = MagicMock()
    mock_bundle.config = {
        "tools": {
            "tool-mcp": {"config": tool_mcp_static_config or {"verbose_servers": False, "max_content_size": 65536}}
        }
    }
    mock_bundle.create_session = _create_session

    return mock_bundle, captured, mock_session


@pytest.mark.asyncio
async def test_mcp_servers_threaded_to_tool_overrides() -> None:
    """mcpServers from params must appear in tool_overrides['tool-mcp']['config']['servers']."""
    from amplifier_agent_lib._runtime import handle_initialize

    mcp_servers = {"test-mcp": {"transport": "stdio", "command": "/usr/bin/echo", "args": ["hello"]}}
    params = _make_params(mcp_servers=mcp_servers)
    mock_bundle, captured, _mock_session = _make_mock_bundle()

    mock_store = MagicMock()
    mock_store.load.return_value = None  # no prior transcript

    with (
        patch(
            "amplifier_agent_lib._runtime.load_and_prepare_cached",
            AsyncMock(return_value=mock_bundle),
        ),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    assert "tool_overrides" in captured, (
        "handle_initialize must pass tool_overrides to create_session. "
        "Add: tool_overrides={'tool-mcp': {'config': tool_mcp_config}} to the create_session call."
    )
    tool_mcp_cfg = captured["tool_overrides"]["tool-mcp"]["config"]
    assert tool_mcp_cfg["servers"] == mcp_servers, (
        f"tool_overrides['tool-mcp']['config']['servers'] should be {mcp_servers!r}, "
        f"got {tool_mcp_cfg.get('servers')!r}"
    )


@pytest.mark.asyncio
async def test_static_tool_mcp_config_merged_with_servers() -> None:
    """Static bundle config keys must be preserved alongside dynamic servers."""
    from amplifier_agent_lib._runtime import handle_initialize

    mcp_servers = {"nano-mcp": {"transport": "sse", "url": "http://localhost:9999"}}
    params = _make_params(mcp_servers=mcp_servers)
    static = {"verbose_servers": False, "max_content_size": 65536}
    mock_bundle, captured, _mock_session = _make_mock_bundle(tool_mcp_static_config=static)

    mock_store = MagicMock()
    mock_store.load.return_value = None

    with (
        patch(
            "amplifier_agent_lib._runtime.load_and_prepare_cached",
            AsyncMock(return_value=mock_bundle),
        ),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    tool_mcp_cfg = captured["tool_overrides"]["tool-mcp"]["config"]
    assert tool_mcp_cfg.get("verbose_servers") is False, "static verbose_servers must be preserved"
    assert tool_mcp_cfg.get("max_content_size") == 65536, "static max_content_size must be preserved"
    assert tool_mcp_cfg.get("servers") == mcp_servers, "servers must be merged in"


@pytest.mark.asyncio
async def test_empty_mcp_servers_still_passes_tool_overrides() -> None:
    """Empty mcpServers must still produce tool_overrides with servers={}."""
    from amplifier_agent_lib._runtime import handle_initialize

    params = _make_params(mcp_servers={})  # empty
    mock_bundle, captured, _mock_session = _make_mock_bundle()

    mock_store = MagicMock()
    mock_store.load.return_value = None

    with (
        patch(
            "amplifier_agent_lib._runtime.load_and_prepare_cached",
            AsyncMock(return_value=mock_bundle),
        ),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    assert "tool_overrides" in captured
    tool_mcp_cfg = captured["tool_overrides"]["tool-mcp"]["config"]
    assert tool_mcp_cfg.get("servers") == {}, "empty mcpServers should produce servers={}"


@pytest.mark.asyncio
async def test_host_capabilities_stored_in_session_metadata() -> None:
    """host.capabilities from params must be stored in session.metadata['host_capabilities']."""
    from amplifier_agent_lib._runtime import handle_initialize

    host_caps = {"supports_structured_errors": True, "supports_steering": False}
    params = _make_params(host_capabilities=host_caps)
    mock_bundle, _captured, mock_session = _make_mock_bundle()

    mock_store = MagicMock()
    mock_store.load.return_value = None

    with (
        patch(
            "amplifier_agent_lib._runtime.load_and_prepare_cached",
            AsyncMock(return_value=mock_bundle),
        ),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    assert mock_session.metadata.get("host_capabilities") == host_caps, (
        f"session.metadata['host_capabilities'] should be {host_caps!r}, "
        f"got {mock_session.metadata.get('host_capabilities')!r}"
    )
