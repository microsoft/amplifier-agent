"""Unit tests for jobbench.matrix: --agent/--task parsing, validation, matrix build.

Fixtures build a synthetic split under a temp JOBBENCH_CACHE_DIR rather than
touching the real (gitignored) dataset-cache -- these tests never fetch or
read real JobBench task/rubric content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobbench import matrix
from jobbench.agents import names as agent_names
from jobbench.dataset import DatasetError


def _write_task(root: Path, occupation: str, task_num: int, *, weight: int = 5) -> None:
    task_dir = root / occupation / f"task{task_num}"
    folder = task_dir / "task_folder"
    folder.mkdir(parents=True)
    (folder / "TASK_INSTRUCTIONS.txt").write_text("do the synthetic thing\n", encoding="utf-8")
    (task_dir / "RUBRICS.json").write_text(
        json.dumps({"rubrics": [{"weight": weight, "criterion": "did the synthetic thing"}]}),
        encoding="utf-8",
    )


@pytest.fixture
def fake_split(tmp_path, monkeypatch):
    """A synthetic 'easy' split: 2 occupations, 3 tasks total. Content is
    entirely fabricated -- no real JobBench task/rubric text.
    """
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("JOBBENCH_CACHE_DIR", str(cache_dir))
    split_dir = cache_dir / "easy"
    _write_task(split_dir, "biostatisticians", 1)
    _write_task(split_dir, "biostatisticians", 2)
    _write_task(split_dir, "lawyer", 1)
    return "easy"


# ---------------------------------------------------------------------------
# --agent parsing / validation
# ---------------------------------------------------------------------------


def test_resolve_agent_names_repeated_flags():
    names = agent_names()
    a, b = names[0], names[1]
    assert matrix.resolve_agent_names([a, b]) == [a, b]


def test_resolve_agent_names_comma_separated():
    names = agent_names()
    a, b = names[0], names[1]
    assert matrix.resolve_agent_names([f"{a},{b}"]) == [a, b]


def test_resolve_agent_names_mixed_repeat_and_comma_dedupes():
    names = agent_names()
    a, b = names[0], names[1]
    # repeated flag AND comma-separated, with a duplicate thrown in -- order
    # of first appearance wins, dupes collapse.
    result = matrix.resolve_agent_names([f"{a},{b}", a])
    assert result == [a, b]


def test_resolve_agent_names_all_expands_from_registry_not_hardcoded():
    assert matrix.resolve_agent_names(["all"]) == agent_names()


def test_resolve_agent_names_unknown_raises_before_anything_else():
    with pytest.raises(matrix.MatrixError, match="unknown agent"):
        matrix.resolve_agent_names(["amplifier-agent", "not-a-real-agent"])


def test_resolve_agent_names_empty_raises():
    with pytest.raises(matrix.MatrixError):
        matrix.resolve_agent_names(None)
    with pytest.raises(matrix.MatrixError):
        matrix.resolve_agent_names([])


# ---------------------------------------------------------------------------
# --task / --all-tasks
# ---------------------------------------------------------------------------


def test_resolve_tasks_repeated_and_comma_selectors(fake_split):
    tasks = matrix.resolve_tasks(
        split=fake_split,
        raw=["biostatisticians/task1,biostatisticians/task2"],
        all_tasks=False,
    )
    assert [t.selector for t in tasks] == ["biostatisticians/task1", "biostatisticians/task2"]


def test_resolve_tasks_all_tasks_wins_over_raw(fake_split):
    tasks = matrix.resolve_tasks(split=fake_split, raw=["ignored/selector"], all_tasks=True)
    assert len(tasks) == 3


def test_resolve_tasks_requires_task_or_all_tasks(fake_split):
    with pytest.raises(matrix.MatrixError):
        matrix.resolve_tasks(split=fake_split, raw=None, all_tasks=False)


def test_resolve_tasks_unknown_selector_raises(fake_split):
    with pytest.raises(DatasetError):
        matrix.resolve_tasks(split=fake_split, raw=["nope/task99"], all_tasks=False)


# ---------------------------------------------------------------------------
# matrix construction
# ---------------------------------------------------------------------------


def test_build_matrix_is_agent_major_cross_product(fake_split):
    tasks = matrix.resolve_tasks(split=fake_split, raw=None, all_tasks=True)
    pairs = matrix.build_matrix(["agent-a", "agent-b"], tasks)
    assert len(pairs) == 2 * len(tasks)
    assert [p.agent for p in pairs[: len(tasks)]] == ["agent-a"] * len(tasks)
    assert [p.agent for p in pairs[len(tasks) :]] == ["agent-b"] * len(tasks)
    assert pairs[0].label == f"agent-a {tasks[0].selector}"


def test_estimate_cost_scales_linearly_with_trial_count():
    low1, mean1, high1 = matrix.estimate_cost(1)
    low10, mean10, high10 = matrix.estimate_cost(10)
    assert low10 == pytest.approx(low1 * 10)
    assert mean10 == pytest.approx(mean1 * 10)
    assert high10 == pytest.approx(high1 * 10)
    assert low1 < mean1 < high1
