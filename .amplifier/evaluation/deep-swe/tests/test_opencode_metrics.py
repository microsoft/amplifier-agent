"""Accuracy guards for the opencode-vanilla arm's token/cost numbers.

Every test here exists because the failure it pins produces a WRONG number
rather than a missing one. A missing number is visible in the summary as `n/a`
and gets investigated; a plausible wrong number gets published.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deepswe_agents.metrics import (
    NOT_AVAILABLE,
    compute_cost_from_tokens,
    normalize_opencode_metrics,
    parse_opencode_db,
)
from deepswe_agents.opencode_vanilla import OpencodeVanillaAgent

SESSION_DDL = """
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    directory TEXT NOT NULL,
    model TEXT,
    cost REAL NOT NULL DEFAULT 0,
    tokens_input INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
    time_created INTEGER,
    time_updated INTEGER
);
CREATE TABLE message (session_id TEXT, data TEXT);
"""


def _make_db(
    path: Path, sessions: list[dict], assistant_turns: dict[str, int] | None = None
) -> str:
    con = sqlite3.connect(path)
    try:
        con.executescript(SESSION_DDL)
        for s in sessions:
            con.execute(
                "INSERT INTO session (id, directory, model, cost, tokens_input,"
                " tokens_output, tokens_cache_read, tokens_cache_write, time_created,"
                " time_updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    s["id"],
                    s["directory"],
                    s.get(
                        "model",
                        json.dumps({"id": "claude-sonnet-5", "providerID": "anthropic"}),
                    ),
                    s.get("cost", 0.0),
                    s.get("tokens_input", 0),
                    s.get("tokens_output", 0),
                    s.get("tokens_cache_read", 0),
                    s.get("tokens_cache_write", 0),
                    s.get("time_created", 1_700_000_000_000),
                    s.get("time_updated", 1_700_000_060_000),
                ),
            )
        for sid, count in (assistant_turns or {}).items():
            for _ in range(count):
                con.execute(
                    "INSERT INTO message (session_id, data) VALUES (?,?)",
                    (sid, json.dumps({"role": "assistant"})),
                )
        con.commit()
    finally:
        con.close()
    return str(path)


# ---------------------------------------------------------------------------
# The workspace filter must not invent a total
# ---------------------------------------------------------------------------


def test_workspace_mismatch_yields_not_available_not_a_wrong_total(tmp_path):
    """A wrong workspace_dir must produce nothing, never everything.

    The old `matched or sessions` fallback summed every session in the database
    when the filter missed -- inflating a real /app run by whatever else the DB
    happened to hold. That is the single most dangerous behavior in this parser.
    """
    db = _make_db(
        tmp_path / "opencode.db",
        [
            {"id": "s1", "directory": "/home/someone", "cost": 4.0, "tokens_input": 5_000_000},
            {"id": "s2", "directory": "/tmp/other", "cost": 2.0, "tokens_input": 3_000_000},
        ],
    )

    parsed = parse_opencode_db([db], workspace_dir="/app")

    assert parsed["files_read"] == 0
    assert parsed["input_tokens"] == 0
    assert parsed["cost_usd"] == 0.0
    assert parsed["cost_from_events"] is False

    record = normalize_opencode_metrics([db], workspace_dir="/app")
    assert record["cost_usd"] == NOT_AVAILABLE
    assert record["input_tokens"] == NOT_AVAILABLE
    assert record["llm_responses"] == NOT_AVAILABLE
    # And it must say WHY, so "no data" is distinguishable from "wrong query".
    assert "none with directory == '/app'" in record["notes"]
    assert "2 session(s)" in record["notes"]


def test_only_matching_workspace_sessions_are_counted(tmp_path):
    """Sessions from other directories must never leak into the task totals."""
    db = _make_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "task",
                "directory": "/app",
                "cost": 1.5,
                "tokens_input": 1000,
                "tokens_output": 200,
                "tokens_cache_read": 50,
                "tokens_cache_write": 75,
            },
            {"id": "noise", "directory": "/root", "cost": 99.0, "tokens_input": 9_999_999},
        ],
        assistant_turns={"task": 7, "noise": 3},
    )

    parsed = parse_opencode_db([db], workspace_dir="/app")

    assert parsed["input_tokens"] == 1000
    assert parsed["output_tokens"] == 200
    assert parsed["cache_read_tokens"] == 50
    assert parsed["cache_write_tokens"] == 75
    # All four are disjoint, so total is their sum: every token processed.
    assert parsed["total_tokens"] == 1000 + 200 + 50 + 75
    assert parsed["llm_responses"] == 7, "assistant turns from /root must not be counted"


# ---------------------------------------------------------------------------
# Cost is recomputed from tokens, never read from opencode's own column
# ---------------------------------------------------------------------------


def test_cost_is_recomputed_and_ignores_opencodes_own_figure(tmp_path):
    """opencode prices from a models.dev card that differs on cache rates.

    See `parse_opencode_db` in metrics.py for why its own figure is ignored.
    """
    db = _make_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "task",
                "directory": "/app",
                # Deliberately absurd: if this leaks into the output, the test fails.
                "cost": 999.0,
                "tokens_input": 1_000_000,
                "tokens_output": 1_000_000,
                "tokens_cache_read": 1_000_000,
                "tokens_cache_write": 1_000_000,
            }
        ],
        assistant_turns={"task": 5},
    )

    # 1M of each at the reference card: 3.00 + 15.00 + 0.30 + 3.75
    expected = 3.00 + 15.00 + 0.30 + 3.75

    parsed = parse_opencode_db([db], workspace_dir="/app")
    assert parsed["cost_usd"] == pytest.approx(expected)
    assert parsed["cost_from_events"] is True

    record = normalize_opencode_metrics([db], workspace_dir="/app")
    assert record["cost_usd"] == pytest.approx(expected)
    assert record["cost_usd"] != pytest.approx(999.0)
    # The divergence must be recorded, not silently swallowed.
    assert "RECOMPUTED" in record["notes"]
    assert "999.000000" in record["notes"], "opencode's own figure must be kept as an audit note"


def test_unknown_model_yields_not_available_not_a_wrong_price(tmp_path):
    """No rate card => no dollar figure. Never 0.0, never opencode's number."""
    db = _make_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "task",
                "directory": "/app",
                "model": json.dumps({"id": "some-future-model", "providerID": "anthropic"}),
                "cost": 7.5,
                "tokens_input": 2_000_000,
            }
        ],
        assistant_turns={"task": 40},
    )

    record = normalize_opencode_metrics([db], workspace_dir="/app")

    assert record["cost_usd"] == NOT_AVAILABLE
    # Tokens are still real and must survive.
    assert record["input_tokens"] == 2_000_000
    assert record["llm_responses"] == 40
    assert "not in the reference rate card" in record["notes"]


def test_compute_cost_from_tokens_matches_the_card():
    assert compute_cost_from_tokens(
        "claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=2_000_000,
        cache_read_tokens=10_000_000,
        cache_write_tokens=4_000_000,
    ) == pytest.approx(3.00 + 30.00 + 3.00 + 15.00)

    assert compute_cost_from_tokens("nope", input_tokens=1_000_000) is None


def _agent_with_model(model: str) -> OpencodeVanillaAgent:
    """Build an adapter without pier's constructor.

    `model` is a read-only property computed from the parsed `--model` value,
    so the backing attribute is what a test can set.
    """
    agent = OpencodeVanillaAgent.__new__(OpencodeVanillaAgent)
    agent._parsed_model_name = model  # type: ignore[attr-defined]
    return agent


def test_opencode_config_pins_cost_and_small_model():
    """The generated opencode.json must not depend on models.dev being reachable."""
    agent = _agent_with_model("claude-sonnet-5")

    config = json.loads(agent._opencode_config())

    # Small model pinned: its default family ends at claude-haiku, which this
    # endpoint does not serve, and the failure kills the process at exit 1.
    assert config["small_model"] == "anthropic/claude-sonnet-5"

    model = config["provider"]["anthropic"]["models"]["claude-sonnet-5"]
    assert model["cost"] == {
        "input": 3.00,
        "output": 15.00,
        "cache": {"read": 0.30, "write": 3.75},
    }


def test_unknown_model_gets_no_fabricated_rate_card():
    """An unpriced model must fall through to `not_available`, not to wrong rates."""
    agent = _agent_with_model("some-future-model")

    config = json.loads(agent._opencode_config())
    assert "cost" not in config["provider"]["anthropic"]["models"]["some-future-model"]


# ---------------------------------------------------------------------------
# Token accounting: the four fields are disjoint and sum to total_tokens
#
# The two sources disagree on what "input" means. opencode's `tokens_input` is
# fresh-only; the amplifier stacks fold cache_read into theirs. Left
# unnormalized with total_tokens = input + output, an opencode trial reported
# 95,147 against an amplifier trial's 1,218,757 on the same run -- an apparent
# 12x gap that inverted the true ordering, because opencode's 19.3M cache reads
# were dropped entirely. These tests pin the normalization on both branches.
# ---------------------------------------------------------------------------


def test_opencode_input_is_fresh_only_and_total_sums_all_four(tmp_path):
    """opencode's column is already fresh-only, so it is accumulated as-is."""
    db = _make_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "s1",
                "directory": "/app",
                "tokens_input": 100,
                "tokens_output": 50,
                "tokens_cache_read": 9_000,
                "tokens_cache_write": 900,
            }
        ],
        assistant_turns={"s1": 3},
    )

    parsed = parse_opencode_db([db], "/app")

    assert parsed["input_tokens"] == 100
    assert parsed["cache_read_tokens"] == 9_000
    assert parsed["cache_write_tokens"] == 900
    assert parsed["total_tokens"] == 100 + 9_000 + 900 + 50


def test_opencode_cost_is_unaffected_by_the_token_normalization(tmp_path):
    """Cost bills cache at cache rates and must not track total_tokens."""
    db = _make_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "s1",
                "directory": "/app",
                "tokens_input": 100,
                "tokens_output": 50,
                "tokens_cache_read": 9_000,
                "tokens_cache_write": 900,
            }
        ],
        assistant_turns={"s1": 1},
    )

    parsed = parse_opencode_db([db], "/app")

    expected = compute_cost_from_tokens(
        "claude-sonnet-5",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=9_000,
        cache_write_tokens=900,
    )
    assert expected is not None
    assert parsed["cost_usd"] == pytest.approx(expected)


def _events_file(tmp_path, usage: dict, response_id: str = "msg_1"):
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event": "llm:response",
                "timestamp": "2026-08-05T15:23:15.602291443+00:00",
                "workspace": "-workspace",
                "data": {
                    "session_id": "fa3b1b70-e043-406a-8c8f-1fbc3edd24f0",
                    "timestamp": "2026-08-05T15:23:15.602291443+00:00",
                    "model": "claude-sonnet-5",
                    "provider": "anthropic",
                    "usage": usage,
                    "raw": {"id": response_id, "role": "assistant", "content": []},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return str(events)


def test_events_branch_strips_cache_read_out_of_input(tmp_path):
    """The amplifier stacks fold cache_read into input; it must come back out.

    Real figures from a run: input=12724 with cache_read=11850 and
    cache_write=504, i.e. 874 genuinely-new. Without the subtraction those
    11,850 cached tokens would be counted twice in total_tokens.
    """
    from deepswe_agents.metrics import parse_events

    path = _events_file(
        tmp_path,
        {
            "input_tokens": 12_724,
            "output_tokens": 72,
            "cache_read_tokens": 11_850,
            "cache_write_tokens": 504,
        },
    )

    parsed = parse_events([path])

    assert parsed["input_tokens"] == 12_724 - 11_850
    assert parsed["cache_read_tokens"] == 11_850
    assert parsed["cache_write_tokens"] == 504
    assert parsed["total_tokens"] == 874 + 11_850 + 504 + 72


def test_events_branch_handles_cache_write_larger_than_input(tmp_path):
    """cache_write is NOT part of input, so a huge write must not go negative.

    This is the shape that disproved the inclusive-of-everything reading:
    input=872 alongside cache_write=12354 on a real first-turn event.
    """
    from deepswe_agents.metrics import parse_events

    path = _events_file(
        tmp_path,
        {
            "input_tokens": 872,
            "output_tokens": 40,
            "cache_read_tokens": 0,
            "cache_write_tokens": 12_354,
        },
    )

    parsed = parse_events([path])

    assert parsed["input_tokens"] == 872
    assert parsed["negative_fresh_input"] == 0
    assert parsed["total_tokens"] == 872 + 0 + 12_354 + 40


def test_events_branch_clamps_and_flags_a_broken_convention(tmp_path):
    """If input < cache_read the assumption broke: clamp, never emit a negative."""
    from deepswe_agents.metrics import normalize_metrics, parse_events

    path = _events_file(
        tmp_path,
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 500,
            "cache_write_tokens": 0,
        },
    )

    parsed = parse_events([path])
    assert parsed["input_tokens"] == 0, "must clamp, not go negative"
    assert parsed["negative_fresh_input"] == 1

    record = normalize_metrics([path], source="test")
    assert "clamped to 0" in record["notes"], "a wrong figure must announce itself"
