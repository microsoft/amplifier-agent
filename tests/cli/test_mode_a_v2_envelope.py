"""Phase A — Mode A v2 JSON envelope tests (unit-level, CliRunner)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def _mock_turn_result(reply: str = "ok") -> dict:
    return {"sessionId": "test-sid", "turnId": "turn-1", "reply": reply}


def test_output_defaults_to_json_envelope_shape() -> None:
    """When --output is omitted, stdout is one JSON envelope per amendment §4.1."""
    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value=_mock_turn_result("hi"),
        ),
        patch(
            "amplifier_agent_cli.modes.single_turn._read_bundle_default_provider",
            return_value="anthropic",
        ),
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "hello"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    # Required top-level fields per §4.1:
    assert "protocolVersion" in envelope
    assert envelope["sessionId"] == "sid-1"
    assert envelope["turnId"] == "turn-1"
    assert envelope["reply"] == "hi"
    assert envelope["error"] is None
    assert "metadata" in envelope
    assert "correlationId" in envelope["metadata"]
    assert "engineVersion" in envelope["metadata"]


def test_output_text_emits_reply_only() -> None:
    """--output text emits the reply on stdout, no JSON envelope. §4.6."""
    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value=_mock_turn_result("plain text reply"),
        ),
        patch(
            "amplifier_agent_cli.modes.single_turn._read_bundle_default_provider",
            return_value="anthropic",
        ),
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "--output", "text", "hello"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "plain text reply"
    # Must NOT be parseable as the JSON envelope:
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


# NOTE: test_mcp_servers_{inline_json_parsed,at_path_form,malformed_json_yields_argv_envelope}
# were removed when the --mcp-servers argv flag was retired. The flag was first
# renamed to --mcp-config-path by PR #24 and then fully removed by PR #29. The
# MCP catalog path now lives in host config (mcp.configPath) or in the
# AMPLIFIER_MCP_CONFIG environment variable; the removal-guardrail for the
# argv flag lives in tests/cli/test_drop_mcp_config_path_flag.py.


def test_protocol_version_mismatch_yields_envelope() -> None:
    """--protocol-version 9.9.9 (mismatch, no skew flag) → error envelope, exit 2."""
    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._read_bundle_default_provider", return_value="anthropic"):
        result = runner.invoke(
            run,
            ["--session-id", "sid-1", "--protocol-version", "9.9.9-NOT-REAL", "hello"],
        )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "protocol_version_mismatch"
    assert envelope["error"]["classification"] == "protocol"
    assert "remediation" in envelope["error"]


def test_protocol_version_skew_suppressed_by_config(tmp_path) -> None:
    """--protocol-version 9.9.9 + config(allowProtocolSkew: true) → no error, normal flow.

    The --allow-protocol-skew argv flag was removed (E3 / D10); the unsafe
    override now lives in the host config file under ``allowProtocolSkew: true``.
    """
    config_file = tmp_path / "host.json"
    config_file.write_text('{"allowProtocolSkew": true}\n', encoding="utf-8")

    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value=_mock_turn_result("ok"),
        ),
        patch("amplifier_agent_cli.modes.single_turn._read_bundle_default_provider", return_value="anthropic"),
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--protocol-version",
                "9.9.9-NOT-REAL",
                "--config",
                str(config_file),
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output


def test_engine_exception_yields_error_envelope_shape() -> None:
    """An AaaError raised by the engine must surface as §4.3 error envelope."""
    from amplifier_agent_lib.protocol.errors import AaaError as EngineAaaError

    async def raise_engine_error(spec):
        raise EngineAaaError("approval_translation_failed", "bad action 'review'")

    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            side_effect=raise_engine_error,
        ),
        patch("amplifier_agent_cli.modes.single_turn._read_bundle_default_provider", return_value="anthropic"),
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "hello"])

    assert result.exit_code == 3, (result.exit_code, result.stdout)
    # exit 3 per §4.4 for classification == 'approval'
    envelope = json.loads(result.stdout)
    assert envelope["error"] is not None
    assert envelope["error"]["code"] == "approval_translation_failed"
    assert envelope["error"]["classification"] == "approval"
    assert envelope["reply"] == ""
    # error.correlationId must equal metadata.correlationId (SC-G)
    assert envelope["error"]["correlationId"] == envelope["metadata"]["correlationId"]
