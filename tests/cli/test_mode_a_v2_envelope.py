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
            "amplifier_agent_cli.provider_detect.detect_provider",
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
            "amplifier_agent_cli.provider_detect.detect_provider",
            return_value="anthropic",
        ),
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "--output", "text", "hello"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "plain text reply"
    # Must NOT be parseable as the JSON envelope:
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_mcp_servers_inline_json_parsed() -> None:
    """--mcp-servers '<json>' parses into the engine's _TurnSpec."""
    runner = CliRunner()
    captured: dict = {}

    async def fake_execute(spec):
        captured["mcp_servers"] = spec.mcp_servers
        return _mock_turn_result("ok")

    with (
        patch("amplifier_agent_cli.modes.single_turn._execute_turn", side_effect=fake_execute),
        patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"),
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--mcp-servers",
                '{"nc_send":{"transport":"stdio","command":"node","args":["/x.js"]}}',
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured["mcp_servers"] == {"nc_send": {"transport": "stdio", "command": "node", "args": ["/x.js"]}}


def test_mcp_servers_at_path_form(tmp_path) -> None:
    """--mcp-servers @<path> reads JSON from a file."""
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        '{"server":{"transport":"stdio","command":"node","args":[]}}',
        encoding="utf-8",
    )
    runner = CliRunner()
    captured: dict = {}

    async def fake_execute(spec):
        captured["mcp_servers"] = spec.mcp_servers
        return _mock_turn_result("ok")

    with (
        patch("amplifier_agent_cli.modes.single_turn._execute_turn", side_effect=fake_execute),
        patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"),
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "--mcp-servers", f"@{cfg}", "hello"])

    assert result.exit_code == 0, result.output
    assert captured["mcp_servers"] == {"server": {"transport": "stdio", "command": "node", "args": []}}


def test_mcp_servers_malformed_json_yields_argv_envelope() -> None:
    """Malformed JSON in --mcp-servers maps to AaaError(argv_json_malformed). O2'."""
    runner = CliRunner()
    with patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--mcp-servers",
                "{not json",
                "hello",
            ],
        )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "argv_json_malformed"
    assert envelope["error"]["classification"] == "protocol"


def test_host_capabilities_threaded_to_envelope() -> None:
    """--host-capabilities '<json>' is echoed in envelope.metadata.hostCapabilities."""
    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value=_mock_turn_result("ok"),
        ),
        patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"),
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--host-capabilities",
                '{"supports_steering":false,"supports_structured_errors":true}',
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["metadata"]["hostCapabilities"] == {
        "supports_steering": False,
        "supports_structured_errors": True,
    }


def test_protocol_version_mismatch_yields_envelope() -> None:
    """--protocol-version 9.9.9 (mismatch, no skew flag) → error envelope, exit 2."""
    runner = CliRunner()
    with patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"):
        result = runner.invoke(
            run,
            ["--session-id", "sid-1", "--protocol-version", "9.9.9-NOT-REAL", "hello"],
        )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "protocol_version_mismatch"
    assert envelope["error"]["classification"] == "protocol"
    assert "remediation" in envelope["error"]


def test_protocol_version_skew_suppressed_by_flag() -> None:
    """--protocol-version 9.9.9 + --allow-protocol-skew → no error, normal flow."""
    runner = CliRunner()
    with (
        patch(
            "amplifier_agent_cli.modes.single_turn._execute_turn",
            return_value=_mock_turn_result("ok"),
        ),
        patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"),
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--protocol-version",
                "9.9.9-NOT-REAL",
                "--allow-protocol-skew",
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
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        side_effect=raise_engine_error,
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
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
