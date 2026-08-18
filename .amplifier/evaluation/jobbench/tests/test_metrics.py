"""Pin the cross-source token-accounting convention.

The two token sources this module normalizes disagree on what "input" means at
the source, so `input_tokens` is normalized to FRESH-ONLY in both branches and
the four token fields are disjoint:

    input_tokens        fresh input only, never previously cached
    cache_read_tokens   input served from cache
    cache_write_tokens  input written into cache
    output_tokens       generated output
    total_tokens        the sum of all four

- opencode's `session.tokens_input` column is already fresh-only, so
  `parse_opencode_db` accumulates it as-is.
- The amplifier stacks fold cache_read INTO their reported `input_tokens` (but
  not cache_write), so `parse_events` subtracts it back out.

These tests pin both sides with synthetic data, so a regression in either
parser is caught here rather than as a silent cross-run number mismatch.

All values below are synthetic and invented for this test; none reflect any
real benchmark task or rubric content.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jobbench.metrics import normalize_metrics, normalize_opencode_metrics


def _build_opencode_db(
    path: Path,
    *,
    directory: str,
    tokens_input: int,
    tokens_output: int,
    tokens_cache_read: int,
    tokens_cache_write: int,
    cost: float = 0.01,
    model: str = "claude-sonnet-5",
) -> None:
    """Write a minimal, synthetic single-session opencode.db to `path`.

    Only the columns `_opencode_sessions`/`parse_opencode_db` actually read
    are populated; this is not a full schema mirror of real opencode.db.
    """
    con = sqlite3.connect(path)
    try:
        con.execute(
            """
            CREATE TABLE session (
                id TEXT,
                directory TEXT,
                tokens_input INTEGER,
                tokens_output INTEGER,
                tokens_cache_read INTEGER,
                tokens_cache_write INTEGER,
                cost REAL,
                model TEXT,
                time_created INTEGER,
                time_updated INTEGER
            )
            """
        )
        con.execute("CREATE TABLE message (session_id TEXT, data TEXT)")
        con.execute(
            """
            INSERT INTO session VALUES
            ('sess-1', ?, ?, ?, ?, ?, ?, ?, 1000, 2000)
            """,
            (
                directory,
                tokens_input,
                tokens_output,
                tokens_cache_read,
                tokens_cache_write,
                cost,
                json.dumps({"id": model, "providerID": "anthropic", "variant": "default"}),
            ),
        )
        con.execute(
            "INSERT INTO message VALUES ('sess-1', ?)",
            (json.dumps({"role": "assistant"}),),
        )
        con.commit()
    finally:
        con.close()


def test_opencode_input_is_fresh_only_and_total_sums_all_four(tmp_path: Path) -> None:
    """opencode's column is already fresh-only, so it is accumulated as-is.

    Shape mirrors the real discrepancy: a tiny tokens_input alongside a huge
    cache_read. The cache reads must land in total_tokens, which the old
    `input + output` formula dropped entirely.
    """
    db_path = tmp_path / "opencode.db"
    _build_opencode_db(
        db_path,
        directory="/workspace",
        tokens_input=134,
        tokens_output=46015,
        tokens_cache_read=4_077_645,
        tokens_cache_write=104_929,
    )

    record = normalize_opencode_metrics(
        [str(db_path)], source="opencode-vanilla-synthetic", workspace_dir="/workspace"
    )

    assert record["input_tokens"] == 134
    assert record["cache_read"] == 4_077_645
    assert record["cache_write"] == 104_929
    assert record["total_tokens"] == 134 + 4_077_645 + 104_929 + 46015


def test_opencode_cost_is_unaffected_by_the_token_normalization(tmp_path: Path) -> None:
    """Cost bills each token type at its own rate and must not track total_tokens."""
    db_path = tmp_path / "opencode.db"
    _build_opencode_db(
        db_path,
        directory="/workspace",
        tokens_input=100,
        tokens_output=10,
        tokens_cache_read=1000,
        tokens_cache_write=0,
        model="claude-sonnet-5",
    )

    record = normalize_opencode_metrics(
        [str(db_path)], source="opencode-vanilla-synthetic", workspace_dir="/workspace"
    )

    # claude-sonnet-5 rates: input $3.00/M, output $15.00/M, cache_read $0.30/M.
    expected_cost = (100 * 3.00 + 10 * 15.00 + 1000 * 0.30) / 1_000_000.0
    assert record["cost_usd"] == round(expected_cost, 6)
    # Pricing 1100 tokens at the plain input rate would be ~10x this. Guard it.
    assert record["cost_usd"] < 0.01


def _write_event(path: Path, usage: dict) -> str:
    event = {
        "event": "llm:response",
        "ts": "2026-01-01T00:00:00.000000+00:00",
        "data": {"session_id": "synthetic-session", "usage": usage},
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    return str(path)


def test_events_branch_strips_cache_read_out_of_input(tmp_path: Path) -> None:
    """The amplifier stacks fold cache_read into input; it must come back out.

    Synthetic analogue of the real event that established the convention:
    input=12724 with cache_read=11850 and cache_write=504, i.e. 874 fresh.
    Without the subtraction those 11,850 cached tokens count twice in total.
    """
    path = _write_event(
        tmp_path / "events.jsonl",
        {
            "input_tokens": 12724,
            "output_tokens": 72,
            "cache_read_tokens": 11850,
            "cache_write_tokens": 504,
            "cost_usd": "0.009147",
        },
    )

    record = normalize_metrics([path], source="amplifier-agent-synthetic")

    assert record["input_tokens"] == 12724 - 11850
    assert record["cache_read"] == 11850
    assert record["cache_write"] == 504
    assert record["total_tokens"] == 874 + 11850 + 504 + 72


def test_events_branch_handles_cache_write_larger_than_input(tmp_path: Path) -> None:
    """cache_write is NOT part of input, so a huge write must not go negative.

    This is the shape that disproved the inclusive-of-everything reading:
    input=872 alongside cache_write=12354 on a real first-turn event.
    """
    path = _write_event(
        tmp_path / "events.jsonl",
        {
            "input_tokens": 872,
            "output_tokens": 40,
            "cache_read_tokens": 0,
            "cache_write_tokens": 12354,
            "cost_usd": "0.01",
        },
    )

    record = normalize_metrics([path], source="amplifier-agent-synthetic")

    assert record["input_tokens"] == 872
    assert record["total_tokens"] == 872 + 0 + 12354 + 40


def test_events_branch_clamps_and_flags_a_broken_convention(tmp_path: Path) -> None:
    """If input < cache_read the assumption broke: clamp, never emit a negative."""
    path = _write_event(
        tmp_path / "events.jsonl",
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 500,
            "cache_write_tokens": 0,
            "cost_usd": "0.001",
        },
    )

    record = normalize_metrics([path], source="amplifier-agent-synthetic")

    assert record["input_tokens"] == 0, "must clamp, not go negative"
    assert "clamped to 0" in record["notes"], "a wrong figure must announce itself"
