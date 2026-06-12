"""Phase A — A2.1'/SC-H audit trail test.

Every turn writes $XDG_STATE_HOME/amplifier-agent/sessions/<sid>/audits/turn-<turnId>.json
with sha256 digests of secret-bearing inputs.

Note: the former ``--mcp-config-path`` argv flag and its companion
``mcpConfigPathDigest`` audit field were removed. MCP config is now
forwarded via the ``AMPLIFIER_MCP_CONFIG`` env var (set by the wrapper or
via ``host_config["mcp"]["configPath"]``); the audit no longer carries
a digest for it.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def test_audit_file_written_with_digests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value={"sessionId": "sid-X", "turnId": "turn-1", "reply": "ok"},
        ),
        patch(
            "amplifier_agent_cli.modes.single_turn._read_bundle_default_provider",
            return_value="anthropic",
        ),
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-X",
                "--workspace",
                "audit-trail-test",
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    audit_path = (
        tmp_path / "state" / "workspaces" / "audit-trail-test" / "sessions" / "sid-X" / "audits" / "turn-turn-1.json"
    )
    assert audit_path.exists(), f"audit file not written at {audit_path}"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    # Required digests (SC-H):
    assert "argvDigest" in audit
    assert "envDigest" in audit
    assert "protocolVersion" in audit
    assert "exitCode" in audit
    assert "correlationId" in audit
    assert "startedAt" in audit and "endedAt" in audit
    # Removed: the former mcpConfigPathDigest field. The argv flag that
    # populated it (``--mcp-config-path``) no longer exists.
    assert "mcpConfigPathDigest" not in audit, (
        "mcpConfigPathDigest must not appear in the audit — the argv flag "
        "that fed it was removed; MCP config now flows via "
        "AMPLIFIER_MCP_CONFIG env var or host_config['mcp']['configPath']."
    )
