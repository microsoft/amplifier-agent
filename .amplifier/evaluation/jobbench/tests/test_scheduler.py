"""Unit tests for jobbench.scheduler: skip-existing, failure isolation, and
the max-parallel concurrency bound.

`trial.run_trial` is monkeypatched to a fake in every test here -- these
never launch a real DTU. Task fixtures point at a nonexistent root; nothing
in these tests touches Task.instructions()/rubrics(), only the filesystem-free
selector/id properties.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jobbench import scheduler
from jobbench.dataset import Task
from jobbench.matrix import Pair
from jobbench.trial import TrialResult


def _task(occupation: str, num: int) -> Task:
    return Task(split="easy", occupation=occupation, task_num=num, root=Path("/nonexistent"))


def _fake_result(agent: str, task: Task, *, status: str = "completed") -> TrialResult:
    return TrialResult(
        agent=agent,
        task_id=task.id,
        task_selector=task.selector,
        split=task.split,
        model="test-model",
        dtu_id="jb-fake-000000",
        image_alias=f"jobbench-{agent}",
        status=status,
        exit_code=0,
        agent_run_s=1.5,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        deliverable_count=1,
        deliverable_bytes=10,
        error=None,
        cost_usd=3.14,
        total_tokens=100,
        llm_responses=1,
        warnings=[],
    )


_COMMON_KWARGS = {
    "model": "m",
    "timeout_s": 10.0,
    "agent_kwargs_for": lambda _agent: {},
    "grade": False,
    "judge_model": "j",
    "judge_api_base": None,
    "judge_api_key": None,
    "judge_max_workers": 1,
    "judge_timeout_per_rubric": 10,
}


async def test_skip_existing_skips_completed_and_reruns_non_completed(tmp_path, monkeypatch):
    run_root = tmp_path / "run"
    pair_done = Pair(agent="agent-a", task=_task("occ", 1))
    pair_stale = Pair(agent="agent-a", task=_task("occ", 2))

    done_dir = run_root / pair_done.agent / pair_done.task.id
    done_dir.mkdir(parents=True)
    (done_dir / "trial.json").write_text(
        json.dumps({"status": "completed", "cost_usd": 1.23, "agent_run_s": 9.0}),
        encoding="utf-8",
    )

    # Not completed (e.g. a previous crash) -- must be re-run, not skipped.
    stale_dir = run_root / pair_stale.agent / pair_stale.task.id
    stale_dir.mkdir(parents=True)
    (stale_dir / "trial.json").write_text(json.dumps({"status": "crashed"}), encoding="utf-8")

    calls: list[str] = []

    async def fake_run_trial(agent, task, trial_dir, **kwargs):
        calls.append(task.id)
        return _fake_result(agent, task)

    monkeypatch.setattr(scheduler.trial_mod, "run_trial", fake_run_trial)

    outcomes = await scheduler.run_matrix(
        [pair_done, pair_stale],
        run_root,
        max_parallel=2,
        skip_existing=True,
        **_COMMON_KWARGS,
    )

    assert calls == [pair_stale.task.id]

    by_id = {o.pair.task.id: o for o in outcomes}
    assert by_id[pair_done.task.id].skipped is True
    assert by_id[pair_done.task.id].cost_usd == 1.23
    assert by_id[pair_stale.task.id].skipped is False
    assert by_id[pair_stale.task.id].status == "completed"


async def test_skip_existing_off_reruns_everything(tmp_path, monkeypatch):
    run_root = tmp_path / "run"
    pair_done = Pair(agent="agent-a", task=_task("occ", 1))
    done_dir = run_root / pair_done.agent / pair_done.task.id
    done_dir.mkdir(parents=True)
    (done_dir / "trial.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    calls: list[str] = []

    async def fake_run_trial(agent, task, trial_dir, **kwargs):
        calls.append(task.id)
        return _fake_result(agent, task)

    monkeypatch.setattr(scheduler.trial_mod, "run_trial", fake_run_trial)

    outcomes = await scheduler.run_matrix(
        [pair_done], run_root, max_parallel=1, skip_existing=False, **_COMMON_KWARGS
    )

    assert calls == [pair_done.task.id]
    assert outcomes[0].skipped is False


async def test_one_trial_raising_does_not_kill_the_batch(tmp_path, monkeypatch):
    run_root = tmp_path / "run"
    pair_bad = Pair(agent="agent-a", task=_task("occ", 1))
    pair_good = Pair(agent="agent-a", task=_task("occ", 2))

    async def fake_run_trial(agent, task, trial_dir, **kwargs):
        if task.id == pair_bad.task.id:
            raise RuntimeError("boom")
        return _fake_result(agent, task)

    monkeypatch.setattr(scheduler.trial_mod, "run_trial", fake_run_trial)

    outcomes = await scheduler.run_matrix(
        [pair_bad, pair_good],
        run_root,
        max_parallel=2,
        skip_existing=False,
        **_COMMON_KWARGS,
    )

    by_id = {o.pair.task.id: o for o in outcomes}
    assert by_id[pair_bad.task.id].status == "crashed"
    assert "boom" in (by_id[pair_bad.task.id].error or "")
    assert by_id[pair_good.task.id].status == "completed"


async def test_max_parallel_bounds_concurrency(tmp_path, monkeypatch):
    run_root = tmp_path / "run"
    pairs = [Pair(agent="agent-a", task=_task("occ", i)) for i in range(1, 5)]

    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_run_trial(agent, task, trial_dir, **kwargs):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        return _fake_result(agent, task)

    monkeypatch.setattr(scheduler.trial_mod, "run_trial", fake_run_trial)

    await scheduler.run_matrix(
        pairs, run_root, max_parallel=2, skip_existing=False, **_COMMON_KWARGS
    )

    assert peak <= 2
    # 4 trials sleeping simultaneously under a cap of 2 should actually bind
    # the cap, not just happen to stay under it by luck of scheduling order.
    assert peak == 2
