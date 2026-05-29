"""Wire-type tests for v0.1.0 — MCP/host extensions (A1).

These tests verify the additive TypedDicts introduced by §4.10.1 of the design:
McpServerConfig, HostCapabilities, InitializeHostParams, and the new
``mcpServers`` and ``host`` fields on ``InitializeParams``.
"""

from __future__ import annotations

import typing


def test_mcp_server_config_typed_dict_exists() -> None:
    """McpServerConfig is a TypedDict with a required ``transport`` field."""
    from amplifier_agent_lib.protocol.methods import McpServerConfig

    # TypedDicts expose __required_keys__ and __optional_keys__
    assert hasattr(McpServerConfig, "__required_keys__")
    assert hasattr(McpServerConfig, "__optional_keys__")
    assert "transport" in McpServerConfig.__required_keys__

    hints = typing.get_type_hints(McpServerConfig, include_extras=True)
    assert "transport" in hints


def test_host_capabilities_typed_dict_exists() -> None:
    """HostCapabilities is a valid TypedDict (total=False, all fields optional)."""
    from amplifier_agent_lib.protocol.methods import HostCapabilities

    assert hasattr(HostCapabilities, "__required_keys__")
    assert hasattr(HostCapabilities, "__optional_keys__")
    # total=False — no required keys
    assert HostCapabilities.__required_keys__ == frozenset()


def test_initialize_params_has_mcp_config_path_field() -> None:
    """InitializeParams declares the optional ``mcpConfigPath`` field (0.2.0)."""
    from amplifier_agent_lib.protocol.methods import InitializeParams

    hints = typing.get_type_hints(InitializeParams, include_extras=True)
    assert "mcpConfigPath" in hints
    assert "mcpServers" not in hints


def test_initialize_params_has_host_field() -> None:
    """InitializeParams declares the optional ``host`` field."""
    from amplifier_agent_lib.protocol.methods import InitializeParams

    hints = typing.get_type_hints(InitializeParams, include_extras=True)
    assert "host" in hints
