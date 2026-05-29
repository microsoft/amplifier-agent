"""Tests for run_output_parser.py: parse_run_output() per §4.1 + §4.4 (SC-D).

Mirror of wrappers/typescript/test/run-output-parser.test.ts.

Six cases exercise the precedence rule from the amendment:
  Rule 1 — envelope parseable: envelope wins, exit code is informational.
    (1a) valid envelope, error=None, exit 0  → result event with reply text
    (1b) valid envelope, error=None, exit 1  → result event (envelope wins)
    (1c) valid envelope, error populated     → error event from envelope fields
  Rule 2 — envelope absent / unparseable: synthesize from exit code + stderr.
    (2a) exit 0 + empty stdout               → envelope_missing / protocol
    (2b) non-zero exit + empty stdout        → engine_exit_<N> / engine
    (2c) partial/truncated JSON              → engine_exit_<N> / engine (rule 2)
"""

from __future__ import annotations

import json
from typing import Any

from amplifier_agent_client.run_output_parser import parse_run_output


def make_envelope(**overrides: Any) -> dict[str, Any]:
    """Helper to build a valid §4.1 envelope with overrides."""
    base: dict[str, Any] = {
        "protocolVersion": "0.2.0",
        "sessionId": "sess-abc-001",
        "turnId": "turn-1",
        "reply": "It is 2:15pm Pacific time.",
        "error": None,
        "metadata": {
            "tokensIn": 1247,
            "tokensOut": 89,
            "durationMs": 1832,
            "bundleDigest": "sha256:7f3a9e2b4c5d6e8f",
            "engineVersion": "0.2.0",
            "protocolVersion": "0.2.0",
            "correlationId": "01HXYZ123ABC456DEF789",
        },
    }
    base.update(overrides)
    return base


def test_1a_valid_envelope_error_none_exit_0_yields_result_event() -> None:
    """(1a) valid envelope with error=None and exit 0 yields result event."""
    env = make_envelope(reply="hello world")
    outcome = {"stdout": json.dumps(env) + "\n", "stderr": "", "exitCode": 0}
    ev = parse_run_output(outcome)
    assert ev["type"] == "result"
    assert ev["text"] == "hello world"


def test_1b_valid_envelope_error_none_exit_1_still_yields_result_envelope_wins() -> None:
    """(1b) valid envelope with error=None and exit 1 still yields result (envelope wins).

    Per §4.4 rule 1: the envelope is authoritative; exit code is informational.
    """
    env = make_envelope(reply="envelope-wins")
    outcome = {"stdout": json.dumps(env), "stderr": "some stderr noise\n", "exitCode": 1}
    ev = parse_run_output(outcome)
    assert ev["type"] == "result"
    assert ev["text"] == "envelope-wins"


def test_1c_valid_envelope_with_populated_error_yields_error_event_from_envelope() -> None:
    """(1c) valid envelope with populated error yields error event from envelope."""
    env = make_envelope(
        reply="",
        error={
            "code": "approval_translation_failed",
            "classification": "approval",
            "severity": "error",
            "correlationId": "01HXYZ123ABC456DEF789",
            "message": ("failed to translate ApprovalRequest to bundle hook shape: unknown approval action 'review'"),
            "stderrTail": "Traceback (most recent call last):\n  ...",
        },
        metadata={
            "tokensIn": 0,
            "tokensOut": 0,
            "durationMs": 247,
            "bundleDigest": "sha256:7f3a9e2b",
            "engineVersion": "0.2.0",
            "protocolVersion": "0.2.0",
            "correlationId": "01HXYZ123ABC456DEF789",
        },
    )
    outcome = {
        "stdout": json.dumps(env),
        "stderr": "ignored when envelope provides stderrTail",
        "exitCode": 3,
    }
    ev = parse_run_output(outcome)
    assert ev["type"] == "error"
    assert ev["code"] == "approval_translation_failed"
    assert ev["classification"] == "approval"
    assert ev["severity"] == "error"
    assert ev["correlationId"] == "01HXYZ123ABC456DEF789"
    assert "failed to translate" in ev["message"]
    assert "Traceback" in ev["stderrTail"]
    assert ev["retryable"] is False


def test_2a_exit_0_empty_stdout_yields_envelope_missing_protocol_error() -> None:
    """(2a) exit 0 + empty stdout yields envelope_missing protocol error."""
    outcome = {"stdout": "", "stderr": "", "exitCode": 0}
    ev = parse_run_output(outcome)
    assert ev["type"] == "error"
    assert ev["code"] == "envelope_missing"
    assert ev["classification"] == "protocol"
    assert ev["severity"] == "error"
    assert ev["retryable"] is False
    assert "envelope" in ev["message"].lower()


def test_2b_non_zero_exit_empty_stdout_yields_engine_exit_with_stderr_tail() -> None:
    """(2b) non-zero exit + empty stdout yields engine_exit_<N> engine error with stderrTail."""
    stderr = "amplifier-agent: provider initialization failed\nstack trace...\n"
    outcome = {"stdout": "", "stderr": stderr, "exitCode": 137}
    ev = parse_run_output(outcome)
    assert ev["type"] == "error"
    assert ev["code"] == "engine_exit_137"
    assert ev["classification"] == "engine"
    assert ev["severity"] == "error"
    assert ev["retryable"] is False
    assert ev["stderrTail"] == stderr


def test_2c_partial_truncated_json_falls_to_rule_2() -> None:
    """(2c) partial/truncated JSON falls to rule 2 (engine_exit_<N>, classification engine).

    Per §4.4 rule 2: belt-and-suspenders — partial JSON is NOT half-parsed.
    """
    outcome = {
        "stdout": '{"protocolVersion":"0.2.0","sessionId":"sess-abc","turnId":"turn-1","reply":"hi"',
        "stderr": "engine died mid-write\n",
        "exitCode": 1,
    }
    ev = parse_run_output(outcome)
    assert ev["type"] == "error"
    assert ev["code"] == "engine_exit_1"
    assert ev["classification"] == "engine"
    assert ev["severity"] == "error"
    assert ev["retryable"] is False


def test_truncates_stderr_tail_to_4096_chars_on_synthesized_engine_errors() -> None:
    """stderrTail is truncated to 4096 chars on synthesized engine errors."""
    long = "X" * 5000 + "TAIL_MARKER"
    outcome = {"stdout": "", "stderr": long, "exitCode": 2}
    ev = parse_run_output(outcome)
    assert ev["type"] == "error"
    assert "stderrTail" in ev
    assert len(ev["stderrTail"]) == 4096
    # Last bytes must be preserved (we keep the *tail*).
    assert ev["stderrTail"].endswith("TAIL_MARKER")
