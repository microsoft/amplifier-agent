"""Unit tests for the judge's SQLite bound and its handling of rubrics it never answered.

No real judge is called and no real rubric or deliverable appears here: databases and rubrics are
synthetic.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jobbench import grading, judge
from jobbench.dataset import Task
from jobbench.grading import GradingError


def _db(path: Path, *, tables: int, rows: int, width: int) -> Path:
    con = sqlite3.connect(path)
    for t in range(tables):
        cols = ", ".join(f"c{i} TEXT" for i in range(width))
        con.execute(f"CREATE TABLE t{t} ({cols})")
        con.executemany(
            f"INSERT INTO t{t} VALUES ({', '.join('?' * width)})",
            [tuple(f"value-{r}-{i}" for i in range(width)) for r in range(rows)],
        )
    con.commit()
    con.close()
    return path


def _rubric() -> dict:
    return {"rubric": "synthetic rubric", "weight": 5, "criterion": ["first", "second"]}


def test_sqlite_within_the_bound_shows_the_upstream_row_limit(tmp_path):
    text = judge.convert_file_to_text(_db(tmp_path / "small.db", tables=2, rows=600, width=2))
    assert len(text) <= judge.SQLITE_MAX_CHARS
    assert (
        text.count(f"shown: {judge.SQLITE_ROWS_PER_TABLE} (LIMIT {judge.SQLITE_ROWS_PER_TABLE})")
        == 2
    )


def test_oversize_sqlite_shows_fewer_rows_and_keeps_every_table(tmp_path):
    text = judge.convert_file_to_text(_db(tmp_path / "big.db", tables=6, rows=600, width=40))
    assert len(text) <= judge.SQLITE_MAX_CHARS
    assert "Content truncated" not in text
    assert all(f"=== Table: t{t} ===" in text for t in range(6))
    assert f"(LIMIT {judge.SQLITE_ROWS_PER_TABLE})" not in text


def test_sqlite_is_cut_when_the_minimum_rows_still_exceed_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "SQLITE_MAX_CHARS", 2_000)
    text = judge.convert_file_to_text(_db(tmp_path / "wide.db", tables=3, rows=50, width=30))
    assert f"(LIMIT {judge.SQLITE_MIN_ROWS_PER_TABLE})" in text
    assert text.endswith("[Content truncated at 2000 characters]")


def test_unanswered_rubric_carries_judge_error(monkeypatch):
    def refuse(api_base, api_key):
        raise RuntimeError("input over the limit")

    monkeypatch.setattr(judge, "get_openai_client", refuse)
    monkeypatch.setattr(judge.time, "sleep", lambda seconds: None)
    result, _ = judge.judge_rubric(0, _rubric(), "content", "judge", "base", "key", max_retries=2)
    assert result["judge_error"] == "input over the limit"
    assert result["result"]["score"] == 0


def test_report_counts_judge_errors():
    answered = judge.build_failed_rubric_result(0, _rubric(), "real fail")
    unanswered = {
        **judge.build_failed_rubric_result(1, _rubric(), "no verdict"),
        "judge_error": "x",
    }
    assert judge.build_details_report("agent", "judge", [answered, unanswered])["judge_errors"] == 1


def test_unanswered_rubrics_are_judged_again(tmp_path):
    details = tmp_path / "judge.json"
    answered = judge.build_failed_rubric_result(0, _rubric(), "real fail")
    unanswered = {
        **judge.build_failed_rubric_result(1, _rubric(), "no verdict"),
        "judge_error": "x",
    }
    details.write_text(json.dumps({"rubrics": [answered, unanswered]}), encoding="utf-8")
    assert set(judge.load_existing_rubric_results(details)) == {0}


def _grade_with_report(tmp_path: Path, monkeypatch, rubrics: list[dict]) -> dict:
    trial_dir = tmp_path / "trial"
    (trial_dir / "deliverables").mkdir(parents=True)

    class Done:
        returncode = 0

    def fake_run(args, **kwargs):
        details = Path(args[args.index("--details-file") + 1])
        details.write_text(
            json.dumps(judge.build_details_report("a", "j", rubrics)), encoding="utf-8"
        )
        return Done()

    monkeypatch.setattr(grading.subprocess, "run", fake_run)
    task = Task(split="main", occupation="synthetic", task_num=1, root=tmp_path / "task")
    return grading.grade(trial_dir, task, agent="a", judge_model="j")


def test_grade_fails_when_the_judge_gave_no_verdict(tmp_path, monkeypatch):
    unanswered = {
        **judge.build_failed_rubric_result(0, _rubric(), "no verdict"),
        "judge_error": "too long",
    }
    with pytest.raises(GradingError, match="no verdict for 1 of 1 rubrics"):
        _grade_with_report(tmp_path, monkeypatch, [unanswered])


def test_grade_returns_a_real_all_fail_report(tmp_path, monkeypatch):
    report = _grade_with_report(
        tmp_path, monkeypatch, [judge.build_failed_rubric_result(0, _rubric(), "fail")]
    )
    assert report["total_score"] == 0
    assert report["judge_errors"] == 0
