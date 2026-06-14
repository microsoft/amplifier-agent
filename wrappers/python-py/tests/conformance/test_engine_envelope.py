"""Conformance — engine §4.1 envelope parsing.

Drives ``parse_run_output`` against canonical engine envelope shapes the
engine emits on stdout when invoked as ``amplifier-agent run --output json``.

These are the wrapper-level counterpart to the JSON-RPC wire fixtures under
``wrappers/conformance/`` — both wrappers (TS and Python) must produce the
same ``DisplayEvent`` for each canonical engine output.  TS coverage lives in
``wrappers/typescript/test/run-output-parser.test.ts``; this file is the
Python mirror.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from amplifier_agent_py import (
    ErrorEvent,
    ResultEvent,
    SubprocessOutcome,
    parse_run_output,
)


def _envelope(
    *,
    reply: str = "",
    error: dict[str, Any] | None = None,
    protocol_version: str = "0.3.0",
    session_id: str = "s-1",
    turn_id: str = "t-1",
    metadata: dict[str, Any] | None = None,
) -> str:
    """Build a canonical §4.1 envelope JSON string."""
    return json.dumps(
        {
            "protocolVersion": protocol_version,
            "sessionId": session_id,
            "turnId": turn_id,
            "reply": reply,
            "error": error,
            "metadata": metadata if metadata is not None else {},
        }
    )


# ---------------------------------------------------------------------------
# Canonical scenarios — success envelopes
# ---------------------------------------------------------------------------


def test_success_envelope_yields_result_with_reply_text() -> None:
    out = SubprocessOutcome(stdout=_envelope(reply="hello world"), stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ResultEvent)
    assert ev.type == "result"
    assert ev.text == "hello world"


def test_success_envelope_overrides_nonzero_exit_code() -> None:
    """Envelope is authoritative — non-zero exit must not change the verdict."""
    out = SubprocessOutcome(stdout=_envelope(reply="ok"), stderr="warn line", exit_code=42)
    ev = parse_run_output(out)
    assert isinstance(ev, ResultEvent)
    assert ev.text == "ok"


def test_success_envelope_with_empty_reply_is_still_result() -> None:
    """A turn that produced no text still yields ResultEvent with empty text."""
    out = SubprocessOutcome(stdout=_envelope(reply=""), stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ResultEvent)
    assert ev.text == ""


# ---------------------------------------------------------------------------
# Canonical scenarios — error envelopes (each classification)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "classification",
    ["transport", "protocol", "engine", "approval", "unknown"],
)
def test_error_envelope_preserves_each_classification(classification: str) -> None:
    out = SubprocessOutcome(
        stdout=_envelope(
            error={
                "code": "tool_execution_failed",
                "classification": classification,
                "severity": "error",
                "correlationId": "cid-x",
                "message": "boom",
            }
        ),
        stderr="",
        exit_code=1,
    )
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.classification == classification


def test_error_envelope_unknown_classification_falls_back_to_unknown() -> None:
    out = SubprocessOutcome(
        stdout=_envelope(
            error={
                "code": "internal",
                "classification": "weird-new-thing",
                "message": "x",
            }
        ),
        stderr="",
        exit_code=1,
    )
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.classification == "unknown"


def test_error_envelope_severity_warning_preserved() -> None:
    out = SubprocessOutcome(
        stdout=_envelope(error={"code": "soft_warn", "severity": "warning", "message": "y"}),
        stderr="",
        exit_code=0,
    )
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.severity == "warning"


def test_error_envelope_missing_message_defaults_to_code() -> None:
    out = SubprocessOutcome(
        stdout=_envelope(error={"code": "no_message_supplied"}),
        stderr="",
        exit_code=1,
    )
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.message == "no_message_supplied"


def test_error_envelope_explicit_stderr_tail_overrides_subprocess_stderr() -> None:
    out = SubprocessOutcome(
        stdout=_envelope(
            error={
                "code": "engine_x",
                "message": "x",
                "stderrTail": "ENVELOPE_TAIL",
            }
        ),
        stderr="DIFFERENT TAIL FROM SUBPROCESS",
        exit_code=1,
    )
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.stderr_tail == "ENVELOPE_TAIL"


# ---------------------------------------------------------------------------
# Canonical scenarios — synthesized errors (Rule 2)
# ---------------------------------------------------------------------------


def test_envelope_absent_with_exit_zero_synthesizes_envelope_missing_with_protocol_classification() -> None:
    out = SubprocessOutcome(stdout="", stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"
    assert ev.classification == "protocol"
    assert ev.severity == "error"
    assert ev.retryable is False


def test_envelope_absent_with_exit_nonzero_synthesizes_engine_exit_n_with_engine_classification() -> None:
    out = SubprocessOutcome(stdout="", stderr="boom\n", exit_code=137)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "engine_exit_137"
    assert ev.classification == "engine"
    assert ev.stderr_tail == "boom\n"


def test_partial_envelope_treated_as_unparseable_belt_and_suspenders() -> None:
    """If any §4.1 required field is missing, fall through to Rule 2."""
    bad = json.dumps(
        {
            "protocolVersion": "0.3.0",
            "sessionId": "s-1",
            "turnId": "t-1",
            # missing "reply"
            "error": None,
            "metadata": {},
        }
    )
    out = SubprocessOutcome(stdout=bad, stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"


def test_partial_envelope_with_missing_metadata_treated_as_unparseable() -> None:
    bad = json.dumps(
        {
            "protocolVersion": "0.3.0",
            "sessionId": "s-1",
            "turnId": "t-1",
            "reply": "ok",
            "error": None,
            # missing "metadata"
        }
    )
    out = SubprocessOutcome(stdout=bad, stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"


def test_envelope_with_wrong_type_for_error_treated_as_unparseable() -> None:
    bad = json.dumps(
        {
            "protocolVersion": "0.3.0",
            "sessionId": "s-1",
            "turnId": "t-1",
            "reply": "ok",
            "error": "not-an-object-or-null",
            "metadata": {},
        }
    )
    out = SubprocessOutcome(stdout=bad, stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"


def test_non_json_stdout_with_exit_zero_synthesizes_envelope_missing() -> None:
    out = SubprocessOutcome(stdout="this is not json", stderr="", exit_code=0)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.code == "envelope_missing"


# ---------------------------------------------------------------------------
# Canonical scenarios — stderr tail handling
# ---------------------------------------------------------------------------


def test_synthesized_error_stderr_tail_truncated_at_4096_bytes() -> None:
    big = "x" * 10000
    out = SubprocessOutcome(stdout="", stderr=big, exit_code=1)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.stderr_tail is not None
    assert len(ev.stderr_tail) == 4096


def test_synthesized_error_short_stderr_tail_preserved_verbatim() -> None:
    out = SubprocessOutcome(stdout="", stderr="short tail", exit_code=1)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.stderr_tail == "short tail"


def test_synthesized_error_empty_stderr_yields_none_tail() -> None:
    out = SubprocessOutcome(stdout="", stderr="", exit_code=1)
    ev = parse_run_output(out)
    assert isinstance(ev, ErrorEvent)
    assert ev.stderr_tail is None
