"""Unit tests for parse_run_output (§4.1 envelope precedence)."""

from __future__ import annotations

import json

from amplifier_agent_py import (
    ErrorEvent,
    ResultEvent,
    SubprocessOutcome,
    parse_run_output,
)


def _envelope(*, reply: str = "ok", error: dict | None = None) -> str:
    return json.dumps(
        {
            "protocolVersion": "0.3.0",
            "sessionId": "s-1",
            "turnId": "t-1",
            "reply": reply,
            "error": error,
            "metadata": {},
        }
    )


def test_valid_envelope_with_null_error_yields_result_event() -> None:
    out = SubprocessOutcome(stdout=_envelope(reply="hello"), stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ResultEvent)
    assert ev.text == "hello"
    assert ev.type == "result"


def test_valid_envelope_with_error_yields_error_event() -> None:
    out = SubprocessOutcome(
        stdout=_envelope(
            error={
                "code": "tool_execution_failed",
                "classification": "engine",
                "severity": "error",
                "correlationId": "cid-1",
                "message": "bad",
            }
        ),
        stderr="",
        exit_code=1,
    )
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "tool_execution_failed"
    assert ev.classification == "engine"
    assert ev.severity == "error"
    assert ev.correlation_id == "cid-1"
    assert ev.message == "bad"


def test_envelope_overrides_exit_code() -> None:
    # Envelope says success — exit code is informational only.
    out = SubprocessOutcome(stdout=_envelope(reply="hi"), stderr="", exit_code=99)
    ev = parse_run_output(out)
    assert isinstance(ev, ResultEvent)
    assert ev.text == "hi"


def test_invalid_envelope_with_exit_zero_synthesizes_envelope_missing() -> None:
    out = SubprocessOutcome(stdout="not json at all", stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"
    assert ev.classification == "protocol"


def test_invalid_envelope_with_nonzero_exit_synthesizes_engine_exit() -> None:
    out = SubprocessOutcome(stdout="", stderr="boom\n", exit_code=137)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "engine_exit_137"
    assert ev.classification == "engine"
    assert ev.stderr_tail == "boom\n"


def test_partial_envelope_missing_required_field_treated_as_unparseable() -> None:
    # Missing 'reply' field — pyright check matches TS belt-and-suspenders rule.
    partial = json.dumps(
        {
            "protocolVersion": "0.3.0",
            "sessionId": "s-1",
            "turnId": "t-1",
            "error": None,
            "metadata": {},
        }
    )
    out = SubprocessOutcome(stdout=partial, stderr="x", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"


def test_stderr_tail_truncated_to_4096_chars_on_synthesized_paths() -> None:
    big = "x" * 10000
    out = SubprocessOutcome(stdout="", stderr=big, exit_code=1)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.stderr_tail is not None
    assert len(ev.stderr_tail) == 4096
