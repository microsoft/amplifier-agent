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
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        return_value=_mock_turn_result("hi"),
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider",
        return_value="anthropic",
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
