"""Regression tests for `llm:response` de-duplication in the metrics pass.

WHY THIS FILE EXISTS. A session that composes more than one logging hook writes
every LLM call to disk more than once. The published `anchors` bundle does this:
it includes both `foundation:behaviors/logging` (-> `<session>/events.jsonl`)
and `context-intelligence:behaviors/context-intelligence-logging` (->
`<session>/context-intelligence/events.jsonl`). Extraction pulls both files and
the metrics pass summed across them, so the amplifier-foundation agent reported
exactly DOUBLE its real calls, tokens and cost -- 20 calls at $2.98 for a trial
that actually made 10 calls at $1.49.

The bug was invisible to every cheap check. The two files share zero identical
lines, because the loggers use different envelope shapes (`ts` vs `timestamp`,
metadata at the top level vs nested under `data`). Only the payload identity
gives it away. These tests pin that behaviour:

  - the two real envelope shapes, carrying one call, count as one
  - distinct calls are still counted separately (the fix must not over-collapse)
  - de-duplication works with raw capture OFF, via the timestamp fingerprint
  - an event with no usable identity is counted rather than silently dropped
  - the correction is stated in `notes`, not applied silently

Run:  uv run python -m pytest tests/ -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepswe_agents.metrics import normalize_metrics, parse_events

# One LLM call, as each of the two loggers actually writes it. Shapes derived
# from real run artifacts.
RESPONSE_ID = "msg_011CdjqAotUMQC2DjuMppaYo"
TIMESTAMP = "2026-08-05T15:23:15.602291443+00:00"
SESSION = "fa3b1b70-e043-406a-8c8f-1fbc3edd24f0"
USAGE = {
    "input_tokens": 2,
    "output_tokens": 398,
    "cache_write_tokens": 16534,
    "cost_usd": "0.1132975",
}


def ci_shape(
    response_id: str = RESPONSE_ID, ts: str = TIMESTAMP, usage: dict | None = None
) -> dict:
    """hook-context-intelligence: `timestamp` inside `data`, `workspace` on top."""
    return {
        "event": "llm:response",
        "timestamp": ts,
        "workspace": "-workspace",
        "data": {
            "session_id": SESSION,
            "timestamp": ts,
            "model": "claude-sonnet-5",
            "provider": "anthropic",
            "status": "ok",
            "duration_ms": 8812,
            "usage": dict(usage or USAGE),
            "raw": {"id": response_id, "role": "assistant", "content": []},
        },
    }


def logging_shape(
    response_id: str = RESPONSE_ID, ts: str = TIMESTAMP, usage: dict | None = None
) -> dict:
    """foundation hooks-logging: `ts` on top, metadata hoisted out of `data`."""
    return {
        "event": "llm:response",
        "ts": ts,
        "lvl": "INFO",
        "status": "ok",
        "duration_ms": 8812,
        "session_id": SESSION,
        "schema": {"name": "amplifier.log", "ver": "1.0.0"},
        "data": {
            "model": "claude-sonnet-5",
            "provider": "anthropic",
            "usage": dict(usage or USAGE),
            "raw": {"id": response_id, "role": "assistant", "content": []},
        },
    }


def write_events(path: Path, events: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(path)


def test_two_loggers_one_call_counts_once(tmp_path):
    """The exact production bug: same call, two files, two envelope shapes."""
    a = write_events(tmp_path / "s" / "context-intelligence" / "events.jsonl", [ci_shape()])
    b = write_events(tmp_path / "s" / "events.jsonl", [logging_shape()])

    parsed = parse_events([a, b])

    assert parsed["llm_responses"] == 1
    assert parsed["duplicate_responses"] == 1
    assert parsed["output_tokens"] == 398
    assert parsed["cost_usd"] == pytest.approx(0.1132975)


def test_distinct_calls_are_not_collapsed(tmp_path):
    """The fix must not over-collapse: understating cost is the worse failure."""
    events = [
        ci_shape(response_id="msg_A", ts="2026-08-05T15:23:15.000000001+00:00"),
        ci_shape(response_id="msg_B", ts="2026-08-05T15:23:16.000000002+00:00"),
        ci_shape(response_id="msg_C", ts="2026-08-05T15:23:17.000000003+00:00"),
    ]
    path = write_events(tmp_path / "events.jsonl", events)

    parsed = parse_events([path])

    assert parsed["llm_responses"] == 3
    assert parsed["duplicate_responses"] == 0
    assert parsed["output_tokens"] == 398 * 3


def test_dedup_without_raw_capture_uses_timestamp_fingerprint(tmp_path):
    """Raw capture is opt-in; de-duplication must still hold when it is off."""

    def strip_raw(event: dict) -> dict:
        event["data"].pop("raw", None)
        return event

    a = write_events(tmp_path / "a" / "events.jsonl", [strip_raw(ci_shape())])
    b = write_events(tmp_path / "b" / "events.jsonl", [strip_raw(logging_shape())])

    parsed = parse_events([a, b])

    assert parsed["llm_responses"] == 1
    assert parsed["duplicate_responses"] == 1


def test_distinct_calls_without_raw_still_distinct(tmp_path):
    """Fingerprint must discriminate on timestamp, not collapse on equal usage."""
    first = ci_shape(ts="2026-08-05T15:23:15.000000001+00:00")
    second = ci_shape(ts="2026-08-05T15:23:16.000000002+00:00")
    for e in (first, second):
        e["data"].pop("raw")
    path = write_events(tmp_path / "events.jsonl", [first, second])

    parsed = parse_events([path])

    assert parsed["llm_responses"] == 2
    assert parsed["duplicate_responses"] == 0


def test_unidentifiable_event_is_counted_not_dropped(tmp_path):
    """No id and no timestamp: count it, flag it, never silently discard it.

    Dropping here would understate cost invisibly. Counting may overstate, which
    is the failure mode that gets noticed and investigated.
    """
    event = {"event": "llm:response", "data": {"usage": {"output_tokens": 10}}}
    path = write_events(tmp_path / "events.jsonl", [event, dict(event)])

    parsed = parse_events([path])

    assert parsed["llm_responses"] == 2
    assert parsed["unidentified_responses"] == 2
    assert parsed["duplicate_responses"] == 0


def test_notes_state_the_correction(tmp_path):
    """A silent correction is one nobody can audit. It must appear in notes."""
    a = write_events(tmp_path / "a" / "events.jsonl", [ci_shape()])
    b = write_events(tmp_path / "b" / "events.jsonl", [logging_shape()])

    record = normalize_metrics([a, b], source="test")

    assert record["llm_responses"] == 1
    assert "Dropped 1 duplicate" in record["notes"]
    assert "more than one logging hook" in record["notes"]


def test_wallclock_unaffected_by_duplicates(tmp_path):
    """Duplicates share timestamps, so the span must not change."""
    early, late = "2026-08-05T15:23:15.000000+00:00", "2026-08-05T15:23:45.000000+00:00"
    single = write_events(
        tmp_path / "one" / "events.jsonl",
        [ci_shape(response_id="a", ts=early), ci_shape(response_id="b", ts=late)],
    )
    dupe = write_events(
        tmp_path / "two" / "events.jsonl",
        [logging_shape(response_id="a", ts=early), logging_shape(response_id="b", ts=late)],
    )

    one_logger = parse_events([single])
    two_loggers = parse_events([single, dupe])

    assert two_loggers["llm_responses"] == one_logger["llm_responses"] == 2
    assert two_loggers["agent_wallclock_s"] == pytest.approx(one_logger["agent_wallclock_s"])
    assert two_loggers["agent_wallclock_s"] == pytest.approx(30.0)
