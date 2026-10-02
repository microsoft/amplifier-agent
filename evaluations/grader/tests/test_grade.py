"""Offline tests for rubric submission, the grader's inputs and its tool set."""

import asyncio
import json
from pathlib import Path

from amplifier_agent import ToolFailed
from amplifier_agent_grader import grade
import pytest

RUBRIC = {"a": {"points": 10, "description": "x"}, "b": {"points": 5, "description": "y  z"}}
LAYOUT = {
    "workspace": "/workspace",
    "driver": "/d",
    "task": "/t.json",
    "sessions": "/s",
    "grader_data": "/g",
    "scratch": "/x",
}


def test_schema() -> None:
    schema = grade.submit_schema(RUBRIC)
    assert schema["required"] == ["a", "b"]
    assert not schema["additionalProperties"]
    assert schema["properties"]["a"]["properties"]["points"]["maximum"] == 10


def test_validate() -> None:
    good = {"a": {"points": 10, "reason": "r"}, "b": {"points": 0, "reason": "r"}}
    assert grade.validate(good, RUBRIC) is None
    assert "missing criteria: b" in str(grade.validate({"a": good["a"]}, RUBRIC))
    assert "between 0 and 5" in str(grade.validate({**good, "b": {"points": 6, "reason": "r"}}, RUBRIC))
    assert "integer" in str(grade.validate({**good, "b": {"points": 2.5, "reason": "r"}}, RUBRIC))
    assert "integer" in str(grade.validate({**good, "b": {"points": True, "reason": "r"}}, RUBRIC))
    assert "unknown criteria: c" in str(grade.validate({**good, "c": {"points": 1, "reason": "r"}}, RUBRIC))


def test_submission_first_valid_wins() -> None:
    submission = grade.Submission(RUBRIC)
    with pytest.raises(ToolFailed):
        asyncio.run(submission.handler({"a": {"points": 1, "reason": "r"}}, None))
    good = {"a": {"points": 10, "reason": "r"}, "b": {"points": 0, "reason": "r"}}
    asyncio.run(submission.handler(good, None))
    asyncio.run(submission.handler({"a": {"points": 0, "reason": "r"}, "b": {"points": 0, "reason": "r"}}, None))
    assert submission.value == good


def test_final_replies() -> None:
    result = {
        "segments": [{"segment": 0, "turns": [{"index": 0, "state": "success", "content": "ready", "error": None}]}],
        "driver_error": {"code": "segment_timeout", "message": "m", "remedy": "r", "traceback": "t"},
    }
    assert grade.final_replies(result) == [
        {"segment": 0, "index": 0, "state": "success", "content": "ready", "error": None},
        {"driver_error": {"code": "segment_timeout", "message": "m", "remedy": "r"}},
    ]
    assert grade.final_replies(None) == []


def test_load_layout(tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(LAYOUT | {"extra": "ignored"}))
    assert grade.load_layout(path) == LAYOUT
    path.write_text(json.dumps({"workspace": "/workspace"}))
    with pytest.raises(ValueError, match="layout needs driver"):
        grade.load_layout(path)


def test_instructions_render_task_and_rubric() -> None:
    evaluation = {"name": "reply", "steps": "  1. Look.\n", "rubric": RUBRIC}
    task = {"name": "hello", "description": "d", "turns": [{"user": "hi"}], "_trial": {"task": "core/hello"}}
    text = grade.instructions(LAYOUT, task, [], evaluation)
    assert "Name: hello" in text
    assert "Steps:\n1. Look." in text
    assert "- b (max 5 points): y z" in text
    assert "/d/events.jsonl" in text
    assert "Expected values" not in text


def test_main_reports_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "layout.json").write_text("{}")
    (tmp_path / "grader.yaml").write_text("evaluations: []\n")
    code = grade.main(
        [
            "--layout", str(tmp_path / "layout.json"),
            "--rubric", str(tmp_path / "grader.yaml"),
            "--out", str(tmp_path / "out"),
            "--provider", "openai",
            "--model", "gpt-6-sol",
        ]
    )  # fmt: skip
    assert code == 1
    assert "grading failed: ValueError" in capsys.readouterr().err
    assert not (tmp_path / "out" / "grader_result.json").exists()
