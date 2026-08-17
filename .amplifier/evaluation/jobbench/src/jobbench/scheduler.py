"""Bounded-concurrency execution of a JobBench (agent, task) matrix.

Ported from amplifier-bundle-evaluation's harness/scheduler.py: an
`asyncio.Semaphore` caps concurrency, one `asyncio.Task` per trial, and
results come back in input order via `asyncio.gather`. The one property that
matters most at 260-trial scale is failure isolation -- a trial raising must
never kill the batch or hold its semaphore slot forever, so every trial is
wrapped in a `try/except` that turns any escape into a recorded failure
instead. `trial.run_trial` already catches everything itself and always
writes trial.json; this is defense in depth for the (grading, skip-check,
scheduler-glue) code that runs alongside it.

No state-machine resume lives here on purpose: recovery is `--run-id` plus
`--skip-existing` re-selecting a subset, not automatic. See run.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobbench import grading
from jobbench import trial as trial_mod
from jobbench.matrix import Pair

logger = logging.getLogger(__name__)

OnLine = Callable[[str], None]
AgentKwargsFor = Callable[[str], dict[str, Any]]


@dataclass
class PairOutcome:
    """What run.py needs to print and tally for one matrix cell.

    This is run bookkeeping (what already lives in trial.json), not the
    cross-task aggregator -- no scoring roll-up beyond the one trial's own
    total_score/max_score.
    """

    pair: Pair
    trial_dir: Path
    skipped: bool
    status: str  # trial status ("completed"/"timeout"/"crashed"/"no_deliverables"), or "skipped"
    agent_run_s: float | None
    cost_usd: float | str
    total_score: float | None
    max_score: float | None
    has_warnings: bool
    error: str | None
    graded_ok: bool


def _read_trial_json(trial_dir: Path) -> dict[str, Any] | None:
    path = trial_dir / "trial.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_completed(trial_dir: Path) -> bool:
    """True when trial_dir already holds a trial.json with status=completed.

    Anything else (missing, unparseable, or a non-completed status such as
    `crashed`/`timeout`/`no_deliverables`) is NOT completed, so --skip-existing
    re-runs it -- only a genuinely finished trial is worth skipping.
    """
    data = _read_trial_json(trial_dir)
    return data is not None and data.get("status") == "completed"


def _skip_outcome(pair: Pair, trial_dir: Path) -> PairOutcome:
    data = _read_trial_json(trial_dir) or {}
    return PairOutcome(
        pair=pair,
        trial_dir=trial_dir,
        skipped=True,
        status="skipped",
        agent_run_s=data.get("agent_run_s"),
        cost_usd=data.get("cost_usd", "not_available"),
        total_score=data.get("total_score"),
        max_score=data.get("max_score"),
        has_warnings=bool(data.get("warnings")),
        error=None,
        graded_ok=True,
    )


async def run_matrix(
    pairs: list[Pair],
    run_root: Path,
    *,
    model: str,
    timeout_s: float,
    max_parallel: int,
    agent_kwargs_for: AgentKwargsFor,
    skip_existing: bool,
    grade: bool,
    judge_model: str,
    judge_api_base: str | None,
    judge_api_key: str | None,
    judge_max_workers: int,
    judge_timeout_per_rubric: int,
    on_line: OnLine | None = None,
) -> list[PairOutcome]:
    """Run every pair in `pairs`, capped at `max_parallel` concurrent trials.

    Each pair writes its own complete artifact set to
    `run_root/<agent>/<task.id>/` exactly as a single-pair `run` always has;
    this function only adds bounded concurrency, skip-existing, and
    per-pair-prefixed progress on top of that. Returns outcomes in the same
    order as `pairs`.
    """
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")

    def emit(line: str) -> None:
        if on_line is not None:
            on_line(line)
        else:
            print(line, flush=True)

    sem = asyncio.Semaphore(max_parallel)

    async def _one(pair: Pair) -> PairOutcome:
        trial_dir = run_root / pair.agent / pair.task.id

        # Skip-existing is checked BEFORE acquiring the semaphore: it's a
        # cheap local read and shouldn't hold a concurrency slot waiting on
        # trials that are actually going to run.
        if skip_existing:
            existing = _read_trial_json(trial_dir)
            if existing is not None and existing.get("status") == "completed":
                emit(f"[{pair.label}] skip-existing: trial.json already status=completed")
                return _skip_outcome(pair, trial_dir)
            if existing is not None:
                emit(
                    f"[{pair.label}] skip-existing: existing trial.json has "
                    f"status={existing.get('status')!r} (not completed) -- re-running"
                )

        async with sem:

            def stage(msg: str) -> None:
                emit(f"[{pair.label}] {msg}")

            try:
                stage("launching trial")
                result = await trial_mod.run_trial(
                    pair.agent,
                    pair.task,
                    trial_dir,
                    model=model,
                    timeout_s=timeout_s,
                    on_stage=stage,
                    agent_kwargs=agent_kwargs_for(pair.agent) or None,
                )
            except Exception as exc:
                # run_trial is supposed to catch everything itself and always
                # write trial.json; this is defensive in case something in
                # the scheduler-facing call itself (not run_trial's own body)
                # escapes -- the batch continues either way.
                logger.exception("trial %s raised outside run_trial's own guard", pair.label)
                stage(f"UNHANDLED ERROR: {type(exc).__name__}: {exc}")
                return PairOutcome(
                    pair=pair,
                    trial_dir=trial_dir,
                    skipped=False,
                    status="crashed",
                    agent_run_s=None,
                    cost_usd="not_available",
                    total_score=None,
                    max_score=None,
                    has_warnings=False,
                    error=f"unhandled in scheduler: {type(exc).__name__}: {exc}",
                    graded_ok=False,
                )

            graded_ok = True
            total_score: float | None = None
            max_score: float | None = None
            if grade:
                stage(f"grading with {judge_model}")
                try:
                    graded_ok = grading.grade_and_record(
                        trial_dir,
                        pair.task,
                        agent=pair.agent,
                        judge_model=judge_model,
                        api_base=judge_api_base,
                        api_key=judge_api_key,
                        max_workers=judge_max_workers,
                        timeout_per_rubric=judge_timeout_per_rubric,
                        on_stage=stage,
                    )
                except Exception as exc:
                    logger.exception("grading raised for %s", pair.label)
                    stage(f"grading UNHANDLED ERROR: {type(exc).__name__}: {exc}")
                    graded_ok = False
                if graded_ok:
                    trial_data = _read_trial_json(trial_dir) or {}
                    total_score = trial_data.get("total_score")
                    max_score = trial_data.get("max_score")

            stage(f"done status={result.status}")
            return PairOutcome(
                pair=pair,
                trial_dir=trial_dir,
                skipped=False,
                status=result.status,
                agent_run_s=result.agent_run_s,
                cost_usd=result.cost_usd,
                total_score=total_score,
                max_score=max_score,
                has_warnings=bool(result.warnings),
                error=result.error,
                graded_ok=graded_ok,
            )

    tasks = [asyncio.create_task(_one(p), name=f"pair:{p.label}") for p in pairs]
    return list(await asyncio.gather(*tasks))


__all__ = ["PairOutcome", "run_matrix"]
