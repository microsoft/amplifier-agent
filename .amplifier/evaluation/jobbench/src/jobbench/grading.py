"""Grade one trial's deliverables against its task's rubric.

Thin wrapper around the JobBench judge (judge.py). Invokes it
DIRECTLY as a module subprocess, never through upstream's `run_judge.sh` --
that script silently SKIPS an empty output directory and writes no result
file at all, making a crashed task indistinguishable from one that was never
run. Calling judge.py directly avoids that: its own `--output-dir` handling
already produces a real all-fail report with "No output files found" for an
empty deliverables directory, which is exactly the honest signal a crashed
or no-deliverables trial should grade to.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from jobbench.dataset import Task

JUDGE_MODULE = Path(__file__).resolve().parent / "judge.py"

DEFAULT_JUDGE_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_WORKERS = 10
DEFAULT_TIMEOUT_PER_RUBRIC = 300


class GradingError(RuntimeError):
    """The judge subprocess could not produce a details report."""


def _safe_name(name: str) -> str:
    """Filesystem-safe fragment for a judge-model-derived filename.

    Judge model ids can carry characters (`/`, `:`) that are fine in an API
    request but not in a path component.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return safe or "judge"


def grade(
    trial_dir: Path,
    task: Task,
    *,
    agent: str,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    api_base: str | None = None,
    api_key: str | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout_per_rubric: int = DEFAULT_TIMEOUT_PER_RUBRIC,
) -> dict:
    """Run the judge on one trial's deliverables.

    Returns the parsed details report (see judge.py's
    `build_details_report`): `total_score`, `max_score`, `passed_count`,
    `total_count`, a `usage` block, and the full per-rubric `rubrics` list.

    Judge stdout/stderr land at `<trial_dir>/grade/judge.log`; the details
    report itself at `<trial_dir>/grade/<safe_judge_model>_judge.json`.

    Raises GradingError if the judge subprocess exits non-zero or does not
    write a details file -- that is a genuine grading failure, distinct from
    a legitimate all-fail report (which judge.py writes and this function
    returns normally).
    """
    grade_dir = trial_dir / "grade"
    grade_dir.mkdir(parents=True, exist_ok=True)
    details_path = grade_dir / f"{_safe_name(judge_model)}_judge.json"
    log_path = grade_dir / "judge.log"

    args = [
        sys.executable,
        str(JUDGE_MODULE),
        "--output-dir",
        str(trial_dir / "deliverables"),
        "--rubrics-file",
        str(task.rubrics_path),
        "--details-file",
        str(details_path),
        "--evaluated-model",
        agent,
        "--judge-model",
        judge_model,
        "--max-workers",
        str(max_workers),
        "--timeout-per-rubric",
        str(timeout_per_rubric),
    ]
    if api_base:
        args.extend(["--api-base", api_base])
    if api_key:
        args.extend(["--api-key", api_key])

    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=False)

    if result.returncode != 0:
        raise GradingError(f"judge exited {result.returncode} for {trial_dir}; see {log_path}")
    if not details_path.is_file():
        raise GradingError(
            f"judge exited 0 but wrote no details file at {details_path}; see {log_path}"
        )

    return json.loads(details_path.read_text(encoding="utf-8"))


def grade_and_record(
    trial_dir: Path,
    task: Task,
    *,
    agent: str,
    judge_model: str,
    api_base: str | None,
    api_key: str | None,
    max_workers: int,
    timeout_per_rubric: int,
    on_stage: Callable[[str], None] | None = None,
) -> bool:
    """Grade one trial and merge the score into its trial.json.

    trial.json's `status` (did it run) and the grade fields (what did it
    score) stay separate keys -- a crashed trial and a trial that legitimately
    scored zero must remain distinguishable from each other. Returns True when
    grading itself ran to completion (any score, including zero); False when
    grading could not run at all (judge crashed, wrote no report) -- that
    failure is recorded as `grade_error`, not folded into `status`.

    A score and the judge that produced it are written together: on success
    `judge_model` is stamped alongside the score; on failure the score fields
    AND `judge_model` are cleared, so no reader can ever pair a score with a
    judge that did not produce it.

    `on_stage`, if given, receives progress/result lines instead of a direct
    print -- the hook a concurrent matrix run uses to prefix every line with
    its (agent, task) pair. Defaults to plain stdout/stderr for the
    single-trial CLI path, unchanged from before this took a hook.
    """

    def emit(msg: str, *, err: bool = False) -> None:
        if on_stage is not None:
            on_stage(msg)
        else:
            print(msg, file=sys.stderr if err else sys.stdout)

    trial_json_path = trial_dir / "trial.json"
    trial_data = (
        json.loads(trial_json_path.read_text(encoding="utf-8")) if trial_json_path.is_file() else {}
    )
    try:
        report = grade(
            trial_dir,
            task,
            agent=agent,
            judge_model=judge_model,
            api_base=api_base,
            api_key=api_key,
            max_workers=max_workers,
            timeout_per_rubric=timeout_per_rubric,
        )
    except GradingError as exc:
        # Clear every grade field, not just set grade_error. This trial.json
        # may already hold a score from an EARLIER grading pass with a
        # different judge; leaving it behind would let a reader pick up that
        # stale number while the run now claims to have been graded by this
        # judge. A missing score is honest; a score attributed to a judge that
        # did not produce it is fabricated provenance, which is strictly worse.
        trial_data["total_score"] = None
        trial_data["max_score"] = None
        trial_data["passed_count"] = None
        trial_data["total_count"] = None
        trial_data["judge_model"] = None
        trial_data["grade_error"] = str(exc)
        trial_json_path.write_text(json.dumps(trial_data, indent=2) + "\n", encoding="utf-8")
        emit(f"error: grading failed for {trial_dir}: {exc}", err=True)
        return False

    trial_data["total_score"] = report.get("total_score")
    trial_data["max_score"] = report.get("max_score")
    trial_data["passed_count"] = report.get("passed_count")
    trial_data["total_count"] = report.get("total_count")
    # Every score carries its own provenance, next to the score itself: a
    # reader never has to consult the run manifest to know which judge
    # produced this number.
    trial_data["judge_model"] = judge_model
    trial_data["grade_error"] = None
    trial_json_path.write_text(json.dumps(trial_data, indent=2) + "\n", encoding="utf-8")
    emit(
        f"score      {report.get('total_score')}/{report.get('max_score')}  "
        f"passed {report.get('passed_count')}/{report.get('total_count')}  ({task.selector})"
    )
    return True


def resolve_credentials(
    api_base_arg: str | None, api_key_arg: str | None
) -> tuple[str | None, str | None]:
    """CLI arg wins; else the OpenAI-style env vars this harness's env sets.

    Kept separate from `grade` itself so callers (run.py) can resolve once
    and log what was used without `grade` reaching into os.environ itself.
    """
    api_base = api_base_arg or os.environ.get("OPENAI_BASE_URL")
    api_key = api_key_arg or os.environ.get("OPENAI_API_KEY")
    return api_base, api_key


__all__ = [
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_TIMEOUT_PER_RUBRIC",
    "GradingError",
    "grade",
    "resolve_credentials",
]
