"""Removal verification tests for the dropped --mcp-config-path argv flag.

The flag was redundant: host_config["mcp"]["configPath"] (set via the host
config file consumed by load_config) and a directly-set $AMPLIFIER_MCP_CONFIG
both produce the same outcome (tool-mcp reads the file). Per Mode A amendment
§2.5 D9 / D10, host policy belongs in host config, not argv.

These tests assert the flag is GONE on both the CLI option surface and the
internal _TurnSpec / make_turn_handler signatures. They will remain as
guardrails after the cleanup lands.
"""

from __future__ import annotations

import importlib
import inspect

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import _TurnSpec, run


def test_mcp_config_path_flag_not_in_help() -> None:
    """`--mcp-config-path` must be absent from `amplifier-agent run --help`."""
    runner = CliRunner()
    result = runner.invoke(run, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--mcp-config-path" not in result.output, (
        "--mcp-config-path flag should be removed from `amplifier-agent run`"
    )


def test_mcp_config_path_flag_rejected_at_usage_time() -> None:
    """Invoking with --mcp-config-path must exit 2 with click's UsageError."""
    runner = CliRunner()
    result = runner.invoke(run, ["--mcp-config-path", "/tmp/x.json", "hi"])
    assert result.exit_code == 2, result.output
    # Click's standard rejection text — pinned to catch accidental tolerance.
    assert "No such option" in result.output and "--mcp-config-path" in result.output, (
        f"Expected click to reject --mcp-config-path with 'No such option'; got: {result.output!r}"
    )


def test_turn_spec_has_no_mcp_config_path_field() -> None:
    """_TurnSpec dataclass must not expose mcp_config_path."""
    # Use dataclass field introspection rather than inspect.signature so the
    # check survives any future signature change (e.g. kwargs-only).
    field_names = {f for f in _TurnSpec.__dataclass_fields__}
    assert "mcp_config_path" not in field_names, (
        f"_TurnSpec must not carry mcp_config_path; found fields: {sorted(field_names)}"
    )


def test_make_turn_handler_has_no_mcp_config_path_kwarg() -> None:
    """make_turn_handler must not accept mcp_config_path as a parameter."""
    runtime = importlib.import_module("amplifier_agent_lib._runtime")
    sig = inspect.signature(runtime.make_turn_handler)
    assert "mcp_config_path" not in sig.parameters, (
        f"make_turn_handler must not accept mcp_config_path; got params: {list(sig.parameters)}"
    )


def test_runtime_source_has_no_cli_flag_handling() -> None:
    """_runtime.py must not reference the CLI-flag mcp_config_path identifier.

    The host-config path (host_config["mcp"]["configPath"]) and the wire
    path (params["mcpConfigPath"] in handle_initialize, surfaced internally
    as the LOCAL variable _wire_mcp_config_path) both stay. We use a regex
    with word boundaries to distinguish the bare CLI-flag name from the
    distinct ``_wire_mcp_config_path`` wire-path variable.
    """
    import re

    runtime = importlib.import_module("amplifier_agent_lib._runtime")
    source = inspect.getsource(runtime)
    # \b matches the start of a word; the lookbehind ensures we don't match
    # inside ``_wire_mcp_config_path`` (which is the wire-path local var).
    matches = re.findall(r"(?<![A-Za-z0-9_])mcp_config_path(?![A-Za-z0-9_])", source)
    assert not matches, (
        "The bare `mcp_config_path` identifier (CLI-flag path) must be removed "
        "from _runtime.py. The wire-side `_wire_mcp_config_path` (in "
        "handle_initialize) and the host_config['mcp']['configPath'] "
        "translation both stay. "
        f"Found {len(matches)} match(es)."
    )
    # The CLI-flag spelling itself must also be gone from comments / docstrings.
    assert "--mcp-config-path" not in source, (
        "The `--mcp-config-path` CLI-flag spelling must not appear in _runtime.py (comments or docstrings)."
    )
