"""Parallel fan-out over a matrix of trials.

The scheduler is intentionally tiny: an `asyncio.Semaphore` caps concurrency at
`max_parallel`, each cell (an `agent x task x trial_index`) runs in its own task,
and results are returned in input order when all are done. There are no events or
pub/sub: an external observer polls each trial's `state.json` (written atomically
by `lifecycle.run_trial`) to watch progress.

Adapted from the reference library's `harness/scheduler.py`
(`amplifier_evaluation.harness.scheduler`), not imported. The differences that
matter for this harness:

- The expensive drivers (AIUser, Extractor, Grader) are composed ONCE by the
  caller and injected into every cell, matching how `run_trial` already takes
  injected building blocks. The Grader is per-task (a task's `grader.yaml`
  selects the implementation), so callers pass a `graders` mapping keyed by
  task id; AIUser and Extractor are task-independent and shared outright.
- Each cell gets a unique, Incus-safe DTU name so trials of the SAME task
  (different agents) launched concurrently never collide on the container name.
- A trial failure never kills the matrix: `run_trial` is defensive and always
  tears its own DTU down, but any escape is still caught here and converted to a
  synthetic `failed` `TrialResult` (with a `state.json` written) so the run tree
  stays complete and consistent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from eval.ai_user import AIUser
from eval.extractor import Extractor
from eval.graders import Grader
from eval.lifecycle import STATE_FILENAME, run_trial
from eval.schema import (
    StageRecord,
    TrialResult,
    TrialSpec,
    TrialState,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

OnTrialFinished = Callable[[TrialResult], None]

# Incus instance names must be valid hostnames: lowercase alphanumerics and
# hyphens, no leading/trailing hyphen, <= 63 chars. We sanitize the agent/task
# ids to that alphabet and add a short random suffix for uniqueness.
_UNSAFE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, max_len: int) -> str:
    """Lowercase `text` to the Incus name alphabet, trimmed to `max_len`."""
    cleaned = _UNSAFE.sub("-", text.lower()).strip("-")
    return cleaned[:max_len].strip("-")


def dtu_name_for(spec: TrialSpec) -> str:
    """A unique, Incus-safe DTU name for one matrix cell.

    Concurrent trials of the same task differ only by agent (and trial index),
    so the name embeds both plus a short random suffix. Kept <= 63 chars.
    """
    base = f"{_slug(spec.agent.id, 20)}-{_slug(spec.task.id, 16)}-t{spec.trial_number}"
    return f"{base}-{uuid.uuid4().hex[:6]}"[:63]


def _synthetic_failed(spec: TrialSpec, error: str) -> TrialResult:
    """Build a `failed` TrialResult for a cell that escaped `run_trial`.

    `run_trial` is supposed to catch everything itself; this is defence in depth
    so one unexpected escape cannot abort the whole matrix.
    """
    now = utcnow_iso()
    return TrialResult(
        trial_id=spec.trial_id,
        agent_id=spec.agent.id,
        task_id=spec.task.id,
        trial_number=spec.trial_number,
        state=TrialState.FAILED.value,
        started_at=now,
        finished_at=now,
        elapsed_s=0.0,
        error=error,
        history=[StageRecord(state=TrialState.FAILED.value, at=now)],
    )


async def run_matrix(
    specs: list[TrialSpec],
    trials_root: Path | str,
    *,
    ai_user: AIUser,
    extractor: Extractor,
    graders: dict[str, Grader],
    max_parallel: int = 4,
    on_finished: OnTrialFinished | None = None,
) -> list[TrialResult]:
    """Run every cell in `specs` concurrently, capped at `max_parallel`.

    Each trial writes its own directory under `trials_root/<trial_id>/` (the
    directory `run_trial` fills with `state.json`, `trial_result.json`,
    `metrics.json`, `extracted/`, `grader/`, and logs). Results are returned in
    input order.

    Args:
        specs: The matrix cells (agent x task x trial_index) to run.
        trials_root: Directory under which each cell's trial dir is created.
        ai_user: A single, already-`setup()` AIUser shared across all cells.
        extractor: A single, already-`setup()` Extractor shared across all cells.
        graders: Already-`setup()` Grader per task id (a task's grader.yaml
            selects the implementation, so graders are keyed by task, not shared
            blindly). Every task referenced by `specs` must have an entry.
        max_parallel: Concurrent cell cap (the Semaphore size). Must be >= 1.
        on_finished: Optional callback invoked with each cell's TrialResult as
            it finishes (never raises out of the scheduler).

    Returns:
        One TrialResult per input spec, in input order. A cell failure is
        captured (state `failed`) and never propagates, so a large matrix
        survives individual trial crashes.
    """
    if max_parallel < 1:
        raise ValueError("max_parallel must be >= 1")

    root = Path(trials_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(max_parallel)

    async def _one(spec: TrialSpec) -> TrialResult:
        async with sem:
            trial_dir = root / spec.trial_id
            trial_dir.mkdir(parents=True, exist_ok=True)
            grader = graders.get(spec.task.id)
            if grader is None:
                result = _synthetic_failed(spec, f"no grader provided for task {spec.task.id!r}")
                _write_state(trial_dir, result)
            else:
                logger.info("starting trial %s", spec.trial_id)
                try:
                    result = await run_trial(
                        spec.agent,
                        spec.task,
                        trial_dir,
                        ai_user=ai_user,
                        extractor=extractor,
                        grader=grader,
                        trial_number=spec.trial_number,
                        dtu_name=dtu_name_for(spec),
                    )
                except Exception as exc:  # defence in depth; run_trial guards itself
                    logger.exception("trial %s raised through run_trial", spec.trial_id)
                    result = _synthetic_failed(
                        spec, f"unhandled in scheduler: {type(exc).__name__}: {exc}"
                    )
                    _write_state(trial_dir, result)
            if on_finished is not None:
                try:
                    on_finished(result)
                except Exception:
                    logger.exception("on_finished callback raised for %s", spec.trial_id)
            return result

    tasks = [asyncio.create_task(_one(s), name=f"trial:{s.trial_id}") for s in specs]
    return list(await asyncio.gather(*tasks))


def _write_state(trial_dir: Path, record: TrialResult) -> None:
    """Persist a synthetic-failure state.json so the run tree stays consistent."""
    try:
        (trial_dir / STATE_FILENAME).write_text(
            json.dumps(asdict(record), indent=2, default=str), encoding="utf-8"
        )
    except OSError:
        logger.exception("could not write state.json for %s", record.trial_id)


__all__ = ["run_matrix", "dtu_name_for"]
