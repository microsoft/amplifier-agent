"""Phase A — A2.1'/SC-H audit trail test.

Every turn writes $XDG_STATE_HOME/amplifier-agent/sessions/<sid>/audits/turn-<turnId>.json
with sha256 digests of secret-bearing inputs.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def test_audit_file_written_with_digests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value={"sessionId": "sid-X", "turnId": "turn-1", "reply": "ok"},
        ),
        patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"),
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-X",
                "--mcp-servers",
                '{"s":{"transport":"stdio","command":"node","args":[],"env":{"K":"SECRET"}}}',
                "--host-capabilities",
                '{"supports_steering":false}',
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    audit_path = tmp_path / "amplifier-agent" / "sessions" / "sid-X" / "audits" / "turn-turn-1.json"
    assert audit_path.exists(), f"audit file not written at {audit_path}"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    # Required digests (SC-H):
    assert "argvDigest" in audit
    assert "mcpServersDigest" in audit
    assert "envDigest" in audit
    assert "hostCapabilities" in audit
    assert "protocolVersion" in audit
    assert "exitCode" in audit
    assert "correlationId" in audit
    assert "startedAt" in audit and "endedAt" in audit
    # Secrets must NOT appear literally:
    full = audit_path.read_text(encoding="utf-8")
    assert "SECRET" not in full
