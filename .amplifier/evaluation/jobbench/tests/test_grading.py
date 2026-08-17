"""Unit tests for jobbench.grading: score/provenance coupling in trial.json.

grading.py is the module that WRITES the scores, so the invariant under test
is not "does the judge judge well" but "can a reader ever pair a score with a
judge that did not produce it". The answer must be no, in both directions:

  - success -> score fields AND `judge_model` written together
  - failure -> score fields AND `judge_model` ALL cleared, `grade_error` set

The failure direction is the one that bites. A re-grade of an existing run
starts from a trial.json that may already hold judge A's score; if judge B
then fails and only `grade_error` is set, the file still shows judge A's
number while the run manifest claims judge B graded the run. That is
fabricated provenance, and it is worse than a missing score.

`grading.grade` (the judge subprocess) is monkeypatched in every test here --
nothing invokes a real judge, spends an API call, or reads a real rubric. All
task/report content is synthetic; no real JobBench task text, rubric text, or
judge output appears in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobbench import grading
from jobbench.dataset import Task
from jobbench.grading import GradingError, _safe_name, grade_and_record


def _task() -> Task:
    """Filesystem-free task handle. Only `.selector` is read on these paths
    (for the progress line), never the task folder or rubric file.
    """
    return Task(split="easy", occupation="biostatisticians", task_num=1, root=Path("/nonexistent"))


def _report(total: float = 7.0, maximum: float = 10.0, passed: int = 2, count: int = 3) -> dict:
    """A synthetic judge details report, shaped like judge.py's
    `build_details_report` output but carrying no rubric text.
    """
    return {
        "total_score": total,
        "max_score": maximum,
        "passed_count": passed,
        "total_count": count,
        "rubrics": [],
    }


def _write_trial(trial_dir: Path, data: dict) -> Path:
    trial_dir.mkdir(parents=True, exist_ok=True)
    path = trial_dir / "trial.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _read_trial(trial_dir: Path) -> dict:
    return json.loads((trial_dir / "trial.json").read_text(encoding="utf-8"))


def _record(trial_dir: Path, *, judge_model: str, stages: list[str] | None = None) -> bool:
    """Call grade_and_record with the arguments run.py passes it."""
    return grade_and_record(
        trial_dir,
        _task(),
        agent="synthetic-agent",
        judge_model=judge_model,
        api_base=None,
        api_key=None,
        max_workers=1,
        timeout_per_rubric=1,
        on_stage=(stages.append if stages is not None else None),
    )


# ---------------------------------------------------------------------------
# _safe_name: judge model ids become a path component
# ---------------------------------------------------------------------------


def test_safe_name_passes_through_ordinary_model_ids():
    assert _safe_name("gpt-5.6-terra") == "gpt-5.6-terra"


def test_safe_name_neutralizes_path_separators():
    """A vendor-prefixed id (`openai/gpt-x`) must not become a subdirectory."""
    result = _safe_name("openai/gpt-x")
    assert "/" not in result
    assert result == "openai-gpt-x"


def test_safe_name_neutralizes_traversal():
    """`../..` must not be able to walk out of the grade directory.

    Dots are deliberately KEPT (real model ids carry them, e.g. `gpt-5.6`),
    so the traversal defense is the separator collapse, not dot removal:
    `../../etc/passwd` becomes one inert filename fragment, not three
    directory levels.
    """
    result = _safe_name("../../etc/passwd")
    assert "/" not in result
    assert result == "..-..-etc-passwd"
    assert not result.startswith("-")


def test_safe_name_neutralizes_windows_separators_and_colons():
    result = _safe_name("azure:models\\gpt-x")
    assert ":" not in result
    assert "\\" not in result


def test_safe_name_never_returns_empty():
    """An id made entirely of stripped characters must still yield a usable
    filename fragment rather than collapsing to '' (which would make the
    details file a bare '_judge.json' or, worse, a directory write).
    """
    assert _safe_name("///") == "judge"
    assert _safe_name("") == "judge"


def test_safe_name_result_is_a_single_path_component(tmp_path: Path):
    """The property that actually matters: joining it stays inside the dir."""
    joined = tmp_path / f"{_safe_name('../../../evil')}_judge.json"
    assert joined.parent == tmp_path


# ---------------------------------------------------------------------------
# grade_and_record: success path
# ---------------------------------------------------------------------------


def test_success_writes_score_and_stamps_the_judge(tmp_path: Path, monkeypatch):
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "completed", "deliverable_count": 3})
    monkeypatch.setattr(grading, "grade", lambda *a, **k: _report())

    assert _record(trial_dir, judge_model="judge-b") is True

    data = _read_trial(trial_dir)
    assert data["total_score"] == 7.0
    assert data["max_score"] == 10.0
    assert data["passed_count"] == 2
    assert data["total_count"] == 3
    assert data["grade_error"] is None
    # Provenance travels with the score, so a reader never has to consult the
    # run manifest to learn which judge produced this number.
    assert data["judge_model"] == "judge-b"


def test_success_preserves_unrelated_trial_fields(tmp_path: Path, monkeypatch):
    """Grading merges into trial.json; it must not rewrite the run record."""
    trial_dir = tmp_path / "trial"
    _write_trial(
        trial_dir,
        {"status": "completed", "agent": "synthetic-agent", "exit_code": 0, "warnings": []},
    )
    monkeypatch.setattr(grading, "grade", lambda *a, **k: _report())

    _record(trial_dir, judge_model="judge-b")

    data = _read_trial(trial_dir)
    assert data["status"] == "completed"
    assert data["agent"] == "synthetic-agent"
    assert data["exit_code"] == 0
    assert data["warnings"] == []


def test_legitimate_zero_score_is_a_success_not_a_failure(tmp_path: Path, monkeypatch):
    """An all-fail report is a real grade. It must return True and record 0,
    NOT be conflated with the judge having failed to run.
    """
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "no_deliverables"})
    monkeypatch.setattr(
        grading, "grade", lambda *a, **k: _report(total=0.0, maximum=10.0, passed=0, count=3)
    )

    assert _record(trial_dir, judge_model="judge-b") is True

    data = _read_trial(trial_dir)
    assert data["total_score"] == 0.0
    assert data["grade_error"] is None
    assert data["judge_model"] == "judge-b"
    assert data["status"] == "no_deliverables"


def test_missing_trial_json_is_created_on_success(tmp_path: Path, monkeypatch):
    """Grading a trial dir whose trial.json never got written still records a
    score rather than raising -- the score is the point.
    """
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir()
    monkeypatch.setattr(grading, "grade", lambda *a, **k: _report())

    assert _record(trial_dir, judge_model="judge-b") is True
    assert _read_trial(trial_dir)["judge_model"] == "judge-b"


# ---------------------------------------------------------------------------
# grade_and_record: failure path -- no stale score may survive
# ---------------------------------------------------------------------------


def test_failed_regrade_does_not_leave_a_prior_judges_score_behind(
    tmp_path: Path, monkeypatch
) -> None:
    """The bug this pins: judge A scored the trial, judge B is asked to
    re-grade, judge B fails. If only `grade_error` were set, trial.json would
    still show 9.0/10 while the run now claims judge B graded it -- a number
    attributed to a judge that never produced it.
    """
    trial_dir = tmp_path / "trial"
    _write_trial(
        trial_dir,
        {
            "status": "completed",
            "total_score": 9.0,
            "max_score": 10.0,
            "passed_count": 3,
            "total_count": 3,
            "judge_model": "judge-a",
            "grade_error": None,
        },
    )

    def _boom(*args, **kwargs):
        raise GradingError("judge exited 1")

    monkeypatch.setattr(grading, "grade", _boom)

    assert _record(trial_dir, judge_model="judge-b") is False

    data = _read_trial(trial_dir)
    assert data["total_score"] is None, "judge A's score survived a failed judge-B re-grade"
    assert data["max_score"] is None
    assert data["passed_count"] is None
    assert data["total_count"] is None
    assert data["judge_model"] is None, "judge A's attribution survived a failed judge-B re-grade"
    assert "judge exited 1" in data["grade_error"]


def test_failure_does_not_clobber_status(tmp_path: Path, monkeypatch):
    """`status` (did it run) and the grade fields (what did it score) are
    separate keys. A grading failure must not overwrite a crash into
    something else, or a crashed trial and a scored-zero trial stop being
    distinguishable.
    """
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "crashed", "exit_code": 1, "error": "synthetic failure"})

    def _boom(*args, **kwargs):
        raise GradingError("judge wrote no details file")

    monkeypatch.setattr(grading, "grade", _boom)

    assert _record(trial_dir, judge_model="judge-b") is False

    data = _read_trial(trial_dir)
    assert data["status"] == "crashed"
    assert data["exit_code"] == 1
    assert data["error"] == "synthetic failure"
    assert data["grade_error"] is not None


def test_failure_after_a_clean_first_grade_still_clears(tmp_path: Path, monkeypatch):
    """Two passes in sequence through the real code path: pass 1 succeeds with
    judge A, pass 2 fails with judge B. Nothing from pass 1 may remain.
    """
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "completed"})

    monkeypatch.setattr(grading, "grade", lambda *a, **k: _report())
    assert _record(trial_dir, judge_model="judge-a") is True
    assert _read_trial(trial_dir)["judge_model"] == "judge-a"

    def _boom(*args, **kwargs):
        raise GradingError("judge exited 137")

    monkeypatch.setattr(grading, "grade", _boom)
    assert _record(trial_dir, judge_model="judge-b") is False

    data = _read_trial(trial_dir)
    assert data["total_score"] is None
    assert data["judge_model"] is None
    assert "137" in data["grade_error"]


def test_only_gradingerror_is_caught(tmp_path: Path, monkeypatch):
    """An unexpected exception is a harness bug, not a grading outcome. It
    must propagate rather than be silently recorded as `grade_error` -- a
    swallowed bug would look identical to a judge that merely failed.
    """
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "completed"})

    def _bug(*args, **kwargs):
        raise ValueError("harness bug, not a grading failure")

    monkeypatch.setattr(grading, "grade", _bug)

    with pytest.raises(ValueError, match="harness bug"):
        _record(trial_dir, judge_model="judge-b")


# ---------------------------------------------------------------------------
# on_stage hook
# ---------------------------------------------------------------------------


def test_on_stage_receives_the_score_line_instead_of_stdout(tmp_path: Path, monkeypatch, capsys):
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "completed"})
    monkeypatch.setattr(grading, "grade", lambda *a, **k: _report())
    stages: list[str] = []

    _record(trial_dir, judge_model="judge-b", stages=stages)

    assert any("score" in line for line in stages)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_on_stage_receives_the_error_line_instead_of_stderr(tmp_path: Path, monkeypatch, capsys):
    trial_dir = tmp_path / "trial"
    _write_trial(trial_dir, {"status": "completed"})

    def _boom(*args, **kwargs):
        raise GradingError("judge exited 1")

    monkeypatch.setattr(grading, "grade", _boom)
    stages: list[str] = []

    _record(trial_dir, judge_model="judge-b", stages=stages)

    assert any("grading failed" in line for line in stages)
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# resolve_credentials
# ---------------------------------------------------------------------------


def test_resolve_credentials_prefers_cli_args_over_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert grading.resolve_credentials("https://arg.invalid", "arg-key") == (
        "https://arg.invalid",
        "arg-key",
    )


def test_resolve_credentials_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert grading.resolve_credentials(None, None) == ("https://env.invalid", "env-key")


def test_resolve_credentials_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert grading.resolve_credentials(None, None) == (None, None)
