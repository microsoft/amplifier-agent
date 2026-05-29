"""Tests for the `amplifier-agent version` CLI subcommand.

TDD bullets (11a):
- `cli version --json` exits 0 with JSON payload containing {protocolVersion, version}
- `cli version` (plain) outputs readable string containing the wire protocol version

The asserted protocol version is sourced from ``amplifier_agent_lib.protocol``
(the wire truth source) so this test moves in lockstep with the engine when
the protocol semver is bumped.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from amplifier_agent_lib.protocol import PROTOCOL_VERSION


def test_version_json_exits_zero_with_payload() -> None:
    """cli version --json exits 0 and emits {protocolVersion, version} JSON."""
    runner = CliRunner()
    result = runner.invoke(cli, ["version", "--json"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output: {result.output}"
    payload = json.loads(result.output.strip())
    assert "protocolVersion" in payload, f"protocolVersion missing from {payload}"
    assert "version" in payload, f"version missing from {payload}"
    assert payload["protocolVersion"] == PROTOCOL_VERSION, (
        f"Expected {PROTOCOL_VERSION!r}, got {payload['protocolVersion']!r}"
    )
    assert isinstance(payload["version"], str) and len(payload["version"]) > 0, (
        f"Expected non-empty version string, got {payload['version']!r}"
    )


def test_version_plain_outputs_protocol_version() -> None:
    """cli version (plain, no --json) outputs the current wire protocol version in stdout."""
    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output: {result.output}"
    assert PROTOCOL_VERSION in result.output, f"Expected {PROTOCOL_VERSION!r} in output, got: {result.output!r}"
