"""Command line entry point for the evaluation harness.

The `validate` subcommand loads every agent and task
definition, resolves all referenced files, checks required fields, and prints a
clear pass/fail report. Run it with:

    uv run python -m eval.cli validate

The `run`, `swebench`, and `automationbench` subcommands run trials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.ai_user import AIUser
from eval.dtu import cli_available
from eval.extractor import Extractor
from eval.graders import make_grader
from eval.install import verify_env
from eval.graders.schema import read_grader_type
from eval.loaders import SpecError, discover_agents, discover_tasks, load_agent
from eval.scheduler import run_matrix
from eval.schema import AgentSpec, TaskSpec, TrialResult, TrialSpec, utcnow_iso
from eval.automation_bench_lifecycle import run_automation_bench_instance
from eval.swe_bench_lifecycle import run_swe_bench_instance
from eval.task_loaders import (
    AutomationBenchTaskLoader,
    SweBenchTaskLoader,
    is_automation_bench_dir,
    is_swe_bench_dir,
)

# The harness root is `evaluation/` (two levels up from this file:
# src/eval/cli.py -> src/eval -> src -> evaluation).
HARNESS_ROOT = Path(__file__).resolve().parents[2]
AGENTS_ROOT = HARNESS_ROOT / "agents"
TASKS_ROOT = HARNESS_ROOT / "tasks"
RUNS_ROOT = HARNESS_ROOT / "runs"

# The walking-skeleton pair used for the live check.
SKELETON_AGENT = "opencode-amplifier-agent"
SKELETON_TASK = "websearch-pdf"

OK = "PASS"
BAD = "FAIL"


def _describe_agent(agent: AgentSpec) -> list[str]:
    return [
        f"    model:      {agent.model}",
        f"    install:    {len(agent.install.setup_cmds)} setup cmd(s), "
        f"required env: {', '.join(agent.install.required_env) or 'none'}",
        f"    invocation: {agent.invocation_md_path.relative_to(HARNESS_ROOT)}",
        f"    extract:    {agent.extract_path.relative_to(HARNESS_ROOT)}",
    ]


def _describe_task(task: TaskSpec) -> list[str]:
    seed = task.seed_dir.relative_to(HARNESS_ROOT) if task.seed_dir else "none"
    return [
        f"    timeout:     {task.timeout_s}s",
        f"    deliverable: {task.deliverable.path}",
        f"    grader:      {task.grader_path.relative_to(HARNESS_ROOT)}",
        f"    profile:     {task.profile_path.relative_to(HARNESS_ROOT)}",
        f"    workspace:   {task.workspace_dir.relative_to(HARNESS_ROOT)}",
        f"    seed:        {seed}",
    ]


def cmd_validate(_: argparse.Namespace) -> int:
    """Load and validate every agent and task; print a pass/fail report."""
    lines: list[str] = []
    failed = False

    lines.append(f"Harness root: {HARNESS_ROOT}")
    lines.append("")

    lines.append(f"Agents ({AGENTS_ROOT.relative_to(HARNESS_ROOT)}/):")
    try:
        agents = discover_agents(AGENTS_ROOT)
        if not agents:
            lines.append(f"  [{BAD}] no agents found")
            failed = True
        for agent_id, agent in agents.items():
            lines.append(f"  [{OK}] {agent_id}")
            lines.extend(_describe_agent(agent))
    except SpecError as exc:
        lines.append(f"  [{BAD}] {exc}")
        failed = True

    lines.append("")
    lines.append(f"Tasks ({TASKS_ROOT.relative_to(HARNESS_ROOT)}/):")
    try:
        tasks = discover_tasks(TASKS_ROOT)
        if not tasks:
            lines.append(f"  [{BAD}] no tasks found")
            failed = True
        for task_id, task in tasks.items():
            group = task.dir.parent.relative_to(TASKS_ROOT)
            lines.append(f"  [{OK}] {task_id}  ({group}/)")
            lines.extend(_describe_task(task))
    except SpecError as exc:
        lines.append(f"  [{BAD}] {exc}")
        failed = True

    # Loader-driven (swe-bench) tasks validate SHALLOWLY: they carry only a
    # meta.yaml (instance_id + dataset + timeout) and are materialized at run time
    # by the swe_bench loader (HuggingFace fetch + Dockerfile -> Incus). `validate`
    # must NOT hit the network, so it checks the meta.yaml shape and that a
    # deterministic grader.yaml resolves -- it does NOT deep-resolve the profile,
    # scenario, or test lists (those require a live fetch). Documented deviation:
    # swe-bench tasks are intentionally not fully resolved here.
    swe_dirs = _discover_swe_bench_dirs(TASKS_ROOT)
    swe_count = 0
    lines.append("")
    lines.append(
        f"Loader-driven tasks ({TASKS_ROOT.relative_to(HARNESS_ROOT)}/, shallow validate):"
    )
    if not swe_dirs:
        lines.append("  (none)")
    for task_dir in swe_dirs:
        try:
            _validate_swe_bench_dir(task_dir)
            swe_count += 1
            lines.append(f"  [{OK}] {task_dir.name}  (swe_bench loader, shallow)")
        except (SpecError, ValueError, FileNotFoundError) as exc:
            lines.append(f"  [{BAD}] {task_dir.name}: {exc}")
            failed = True

    # AutomationBench tasks also validate SHALLOWLY: they carry only a meta.yaml
    # (benchmark + task + timeout) selector. Their task_info is NOT vendored -- it
    # is fetched on the fly by the automation_bench loader at run time (which also
    # synthesizes the profile + installs AutomationBench in the DTU). `validate`
    # checks the meta.yaml shape and that the shared automation_bench grader.yaml
    # resolves -- it does NOT fetch task data or launch a DTU.
    ab_dirs = _discover_automation_bench_dirs(TASKS_ROOT)
    ab_count = 0
    lines.append("")
    lines.append(
        f"AutomationBench tasks ({TASKS_ROOT.relative_to(HARNESS_ROOT)}/, shallow validate):"
    )
    if not ab_dirs:
        lines.append("  (none)")
    for task_dir in ab_dirs:
        try:
            _validate_automation_bench_dir(task_dir)
            ab_count += 1
            lines.append(f"  [{OK}] {task_dir.name}  (automation_bench loader, shallow)")
        except (SpecError, ValueError, FileNotFoundError) as exc:
            lines.append(f"  [{BAD}] {task_dir.name}: {exc}")
            failed = True

    lines.append("")
    if failed:
        lines.append(f"VALIDATE {BAD}: one or more definitions are invalid.")
    else:
        lines.append(
            f"VALIDATE {OK}: {len(agents)} agent(s), {len(tasks)} static task(s), "
            f"{swe_count} swe-bench task(s), and {ab_count} automation-bench task(s) "
            f"loaded and all referenced files resolved."
        )

    print("\n".join(lines))
    return 1 if failed else 0


def _discover_swe_bench_dirs(tasks_root: Path) -> list[Path]:
    """Find every loader-driven (swe-bench) instance dir under `tasks_root`.

    A swe-bench dir has a `meta.yaml` with `instance_id` + `dataset` and no
    `task.yaml` (see `is_swe_bench_dir`). Returns them sorted for a stable report.
    """
    if not tasks_root.is_dir():
        return []
    found = [meta.parent for meta in tasks_root.rglob("meta.yaml") if is_swe_bench_dir(meta.parent)]
    return sorted(found)


def _validate_swe_bench_dir(task_dir: Path) -> None:
    """Shallow-validate one swe-bench instance dir (no network fetch).

    Checks the meta.yaml shape (instance_id + dataset) and that a deterministic
    grader.yaml resolves. Raises on the first problem. Does NOT fetch the row,
    convert the Dockerfile, or resolve test lists -- those need a live fetch and
    are deferred to the swe_bench loader at run time.
    """
    loader = SweBenchTaskLoader()
    loader.read_meta(task_dir)  # raises ValueError on a malformed meta.yaml
    from eval.task_loaders.swe_bench import _resolve_grader_path

    grader_path = _resolve_grader_path(task_dir)  # raises FileNotFoundError if absent
    grader_type = read_grader_type(grader_path)
    if grader_type != "deterministic":
        raise ValueError(
            f"expected a deterministic grader for a swe-bench task, got type={grader_type!r} "
            f"at {grader_path}"
        )


def _discover_automation_bench_dirs(tasks_root: Path) -> list[Path]:
    """Find every automation-bench task dir under `tasks_root`.

    An automation-bench dir has a `meta.yaml` with `benchmark: automation-bench`
    and no `task.yaml` (see `is_automation_bench_dir`). Returns them sorted for a
    stable report.
    """
    if not tasks_root.is_dir():
        return []
    found = [
        meta.parent
        for meta in tasks_root.rglob("meta.yaml")
        if is_automation_bench_dir(meta.parent)
    ]
    return sorted(found)


def _validate_automation_bench_dir(task_dir: Path) -> None:
    """Shallow-validate one automation-bench task dir (no DTU launch, no fetch).

    Checks the meta.yaml shape (benchmark + task) and that the shared
    automation_bench grader.yaml resolves with the right type. Raises on the first
    problem. Does NOT fetch task_info (that is pulled on the fly by the loader at
    run time), synthesize the profile, or install AutomationBench -- those need a
    live fetch/DTU and are deferred to the automation_bench loader at run time.
    """
    loader = AutomationBenchTaskLoader()
    loader.read_meta(task_dir)  # raises ValueError on a malformed meta.yaml

    grader_path = task_dir.parent / "grader.yaml"
    if not grader_path.is_file():
        raise FileNotFoundError(
            f"no automation-bench grader.yaml found for {task_dir} (looked at {grader_path})"
        )
    grader_type = read_grader_type(grader_path)
    if grader_type != "automation_bench":
        raise ValueError(
            f"expected an automation_bench grader for an automation-bench task, "
            f"got type={grader_type!r} at {grader_path}"
        )


def _ts() -> str:
    """Short wall-clock timestamp for progress lines in the check log."""
    return datetime.now(UTC).strftime("%H:%M:%S")


def _log(line: str) -> None:
    """Print a timestamped progress line, flushed so background logs stay live."""
    print(f"[{_ts()}] {line}", flush=True)


# ---------------------------------------------------------------------------
# The matrix `run` subcommand (scheduler-backed parallel fan-out).
# ---------------------------------------------------------------------------

# Keys that must be summarized (not dumped whole) into combined-summary.json.
_METRIC_KEYS = (
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "llm_responses",
    "agent_wallclock_s",
    "agent_wallclock_events_span_s",
    "total_wallclock_s",
)


def _is_secret_key(name: str) -> bool:
    """True if an env var NAME looks like it holds a secret value.

    Mirrors the reference library's redaction predicate: any var whose name
    contains KEY / TOKEN / SECRET / PASSWORD is treated as secret.
    """
    upper = name.upper()
    return any(tok in upper for tok in ("TOKEN", "KEY", "SECRET", "PASSWORD"))


def _redact_env(names: list[str]) -> dict[str, str]:
    """Snapshot the given env vars, redacting secret-looking VALUES.

    A var whose name looks secret is recorded as `<redacted>` when set (never the
    literal value) or `<unset>` when absent. Non-secret vars record their literal
    value. This is what lands in the persisted plan.json, so a run record never
    leaks an API key while still documenting which env the run depended on.
    """
    snapshot: dict[str, str] = {}
    for name in sorted(set(names)):
        present = bool(os.environ.get(name))
        if _is_secret_key(name):
            snapshot[name] = "<redacted>" if present else "<unset>"
        else:
            snapshot[name] = os.environ.get(name, "<unset>")
    return snapshot


def _parse_csv(value: str | None) -> list[str]:
    """Split a comma list like `a,b,c` into `['a','b','c']` (empty -> [])."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _cell_summary(result: TrialResult, out: Path) -> dict[str, Any]:
    """One row of combined-summary.json: status + score + key metrics for a cell.

    Reads the cell's consolidated `trial_result.json` (written by `run_trial`) for
    the grader score and metrics; falls back to the in-memory TrialResult for
    status/timing so a cell that never produced a record still appears.
    """
    trial_dir = out / result.trial_id
    overall_score: float | None = None
    metrics_summary: dict[str, Any] | None = None
    rec_path = trial_dir / "trial_result.json"
    if rec_path.is_file():
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rec = {}
        overall_score = (rec.get("score") or {}).get("overall_score")
        metrics = rec.get("metrics") or {}
        if metrics:
            metrics_summary = {k: metrics.get(k) for k in _METRIC_KEYS}
    return {
        "trial_id": result.trial_id,
        "agent_id": result.agent_id,
        "task_id": result.task_id,
        "trial_number": result.trial_number,
        "status": result.state,
        "dtu_id": result.dtu_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "overall_score": overall_score,
        "metrics": metrics_summary,
        "error": result.error,
        "trial_dir": str(trial_dir),
    }


async def _progress_poller(
    specs: list[TrialSpec], out: Path, stop_event: asyncio.Event, interval: float = 20.0
) -> None:
    """Every `interval`s, log each cell's current state.json state until stopped.

    This is the concurrency-evidence surface: an external log reader sees several
    trials sitting in `running_agent` at the same wall-clock tick, proving the
    fan-out is real rather than serial.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return  # stop_event set: final states already logged by on_finished
        except asyncio.TimeoutError:
            pass
        parts: list[str] = []
        for s in specs:
            state = "?"
            sp = out / s.trial_id / "state.json"
            if sp.is_file():
                try:
                    state = str(json.loads(sp.read_text(encoding="utf-8")).get("state", "?"))
                except (OSError, json.JSONDecodeError):
                    state = "?"
            parts.append(f"{s.agent.id}/t{s.trial_number}={state}")
        _log("progress: " + "  |  ".join(parts))


async def _run_matrix_command(
    specs: list[TrialSpec], out: Path, max_parallel: int
) -> list[TrialResult]:
    """Compose the shared drivers ONCE, then fan the matrix out via the scheduler.

    Builds and `setup()`s a single AI User and Extractor (task-independent) plus
    one Grader per distinct task (a task's grader.yaml selects the type), then
    hands them to `scheduler.run_matrix`. Every cell reuses these composed bricks;
    each launches its own DTU and always tears it down (guaranteed by run_trial).
    """
    _log("setting up shared AI User (compose foundation + anthropic-opus-4-8)...")
    ai_user = AIUser()
    await ai_user.setup()
    _log("setting up shared Extractor (compose foundation + anthropic-sonnet)...")
    extractor = Extractor()
    await extractor.setup()

    # One grader per distinct task; setup once, shared across that task's cells.
    tasks_by_id = {s.task.id: s.task for s in specs}
    graders: dict[str, Any] = {}
    for task_id, task in tasks_by_id.items():
        grader = make_grader(task.grader_path)
        _log(f"setting up grader type={grader.grader_type} for task {task_id}...")
        await grader.setup()
        graders[task_id] = grader
    _log(
        f"drivers ready; fanning out {len(specs)} trial(s) at max_parallel={max_parallel} "
        "(each launches its own DTU; real minutes + API cost)..."
    )

    finished: list[str] = []

    def _on_finished(result: TrialResult) -> None:
        finished.append(result.trial_id)
        score = None
        rec_path = out / result.trial_id / "trial_result.json"
        if rec_path.is_file():
            try:
                score = (json.loads(rec_path.read_text(encoding="utf-8")).get("score") or {}).get(
                    "overall_score"
                )
            except (OSError, json.JSONDecodeError):
                score = None
        _log(
            f"[{len(finished)}/{len(specs)}] trial finished: {result.trial_id} "
            f"state={result.state} score={score}"
        )

    stop_event = asyncio.Event()
    poller = asyncio.create_task(_progress_poller(specs, out, stop_event), name="matrix:progress")
    try:
        results = await run_matrix(
            specs,
            out,
            ai_user=ai_user,
            extractor=extractor,
            graders=graders,
            max_parallel=max_parallel,
            on_finished=_on_finished,
        )
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(poller, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            poller.cancel()
    return results


def cmd_run(args: argparse.Namespace) -> int:
    """Run a matrix of trials (agents x tasks x trials) in parallel.

    Selection is the cartesian product of `--agents`, `--tasks`, and `--trials`
    (comma lists; each defaults to every discovered agent/task and one trial). The
    scheduler fans the cells out under an `asyncio.Semaphore(max_parallel)` cap,
    reusing one composed AI User / Extractor and per-task Grader across all cells.
    Writes a top-level `plan.json` (the selected matrix + redacted env) up front,
    per-trial dirs (`<trial_id>/`) as they run, and a `combined-summary.json`
    (per-cell status + score + key metrics) at the end. Exit 0 only when every
    cell reaches `completed`.
    """
    try:
        agents = discover_agents(AGENTS_ROOT)
        tasks = discover_tasks(TASKS_ROOT)
    except SpecError as exc:
        _log(f"{BAD}: could not load definitions: {exc}")
        return 1

    if args.list_agents or args.list_tasks:
        if args.list_agents:
            print("agents:")
            for aid in sorted(agents):
                print(f"  {aid}")
        if args.list_tasks:
            print("tasks:")
            for tid in sorted(tasks):
                print(f"  {tid}")
        return 0

    agent_ids = _parse_csv(args.agents) or sorted(agents)
    task_ids = _parse_csv(args.tasks) or sorted(tasks)
    trials = int(args.trials)
    max_parallel = int(args.max_parallel)

    if trials < 1:
        _log(f"{BAD}: --trials must be >= 1 (got {trials})")
        return 1
    if max_parallel < 1:
        _log(f"{BAD}: --max-parallel must be >= 1 (got {max_parallel})")
        return 1

    unknown_agents = [a for a in agent_ids if a not in agents]
    unknown_tasks = [t for t in task_ids if t not in tasks]
    if unknown_agents:
        _log(f"{BAD}: unknown agent(s) {unknown_agents}; known: {', '.join(sorted(agents))}")
        return 1
    if unknown_tasks:
        _log(f"{BAD}: unknown task(s) {unknown_tasks}; known: {', '.join(sorted(tasks))}")
        return 1

    # Selection = agents x tasks x trials, in a stable, documented order.
    specs: list[TrialSpec] = [
        TrialSpec(agent=agents[a], task=tasks[t], trial_number=n)
        for a in agent_ids
        for t in task_ids
        for n in range(1, trials + 1)
    ]

    # Preflight: no point composing expensive drivers if the run can't proceed.
    if not cli_available():
        _log(f"{BAD}: `amplifier-digital-twin` not on PATH")
        return 1
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        _log(f"{BAD}: ANTHROPIC_API_KEY not set on the host (AI User/extractor/grader need it)")
        return 1
    required_env: list[str] = []
    for a in agent_ids:
        missing = verify_env(agents[a])
        if missing:
            _log(f"{BAD}: agent {a} missing required host env: {', '.join(missing)}")
            return 1
        required_env.extend(agents[a].install.required_env)

    if args.output_dir:
        out = Path(args.output_dir).expanduser().resolve()
    else:
        out = RUNS_ROOT / "matrix" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    run_id = out.name
    started_at = utcnow_iso()

    # Persist the plan up front so an observer can correlate trial dirs mid-run.
    plan = {
        "run_id": run_id,
        "created_at": started_at,
        "output_dir": str(out),
        "max_parallel": max_parallel,
        "trials_per_cell": trials,
        "agents": agent_ids,
        "tasks": task_ids,
        "cell_count": len(specs),
        "matrix": [
            {
                "trial_id": s.trial_id,
                "agent": s.agent.id,
                "task": s.task.id,
                "trial_number": s.trial_number,
                "trial_dir": s.trial_id,
            }
            for s in specs
        ],
        "env": _redact_env(required_env),
    }
    (out / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    _log(f"matrix run: {len(specs)} cell(s), max_parallel={max_parallel}")
    _log(f"  agents: {', '.join(agent_ids)}")
    _log(f"  tasks:  {', '.join(task_ids)}")
    _log(f"  output: {out}")
    _log(f"  plan:   {out / 'plan.json'}")

    results = asyncio.run(_run_matrix_command(specs, out, max_parallel))

    # Combined summary: per-cell status + score + key metrics, plus state counts.
    counts: dict[str, int] = {}
    for r in results:
        counts[r.state] = counts.get(r.state, 0) + 1
    finished_at = utcnow_iso()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "max_parallel": max_parallel,
        "cell_count": len(results),
        "counts": counts,
        "cells": [_cell_summary(r, out) for r in results],
    }
    (out / "combined-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    completed = counts.get("completed", 0)
    _log("")
    _log(f"--- matrix complete: {completed}/{len(results)} cell(s) completed ---")
    for cell in summary["cells"]:
        metrics = cell.get("metrics") or {}
        _log(
            f"  {cell['status']:>10}  {cell['trial_id']}  "
            f"score={cell['overall_score']}  cost_usd={metrics.get('cost_usd')}"
        )
    _log(f"combined summary: {out / 'combined-summary.json'}")

    if completed == len(results):
        _log(f"RUN {OK}: all {len(results)} cell(s) completed; run tree written to {out}.")
        return 0
    _log(f"RUN {BAD}: {len(results) - completed} cell(s) did not complete (see summary above).")
    return 1


# ---------------------------------------------------------------------------
# The loader-driven `swebench` subcommand (deterministic grading).
# ---------------------------------------------------------------------------

SWE_INSTANCES_ROOT = TASKS_ROOT / "swe-bench-pro" / "instances"

# element-web is a known-broken instance: its instance Dockerfile
# writes but never runs build.sh AND the jest suite OOMs; keep it excluded here so
# an operator cannot accidentally pick it.
_EXCLUDED_INSTANCE_SUBSTR = "element-web"


def _resolve_swe_instance_dir(instance: str) -> Path | None:
    """Resolve an instance id OR a path to its instance dir under swe-bench-pro/.

    Accepts (a) a filesystem path to an instance dir, or (b) an instance_id that
    names a dir under `tasks/swe-bench-pro/instances/`, or (c) an instance_id
    matched against the `instance_id` field of any discovered meta.yaml.
    """
    p = Path(instance).expanduser()
    if p.is_dir() and is_swe_bench_dir(p):
        return p.resolve()
    direct = SWE_INSTANCES_ROOT / instance
    if direct.is_dir() and is_swe_bench_dir(direct):
        return direct.resolve()
    for task_dir in _discover_swe_bench_dirs(TASKS_ROOT):
        try:
            meta = SweBenchTaskLoader().read_meta(task_dir)
        except (ValueError, OSError):
            continue
        if str(meta.get("instance_id")) == instance:
            return task_dir.resolve()
    return None


def cmd_swebench(args: argparse.Namespace) -> int:
    """Gate: run one swe-bench instance (gold or agent) end to end.

    Loader-driven: fetches the instance from HuggingFace, converts the official
    Dockerfiles into an Incus profile, launches it, then (gold) applies the
    dataset reference patch or (agent) installs + drives the agent-under-test and
    captures its patch, and grades deterministically by test pass/fail. Always
    tears the DTU down. Exit 0 only when grading produced a verdict.
    """
    # The swe-bench lifecycle reports progress via the logging module (long
    # provisioning + grading phases). Configure a handler so those INFO lines
    # reach the (often backgrounded) stdout log; other subcommands are unaffected.
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    task_dir = _resolve_swe_instance_dir(args.instance)
    if task_dir is None:
        _log(f"{BAD}: could not resolve swe-bench instance {args.instance!r}")
        _log(f"    known instances under {SWE_INSTANCES_ROOT}:")
        for d in _discover_swe_bench_dirs(TASKS_ROOT):
            _log(f"      {d.name}")
        return 1
    if _EXCLUDED_INSTANCE_SUBSTR in task_dir.name:
        _log(
            f"{BAD}: instance {task_dir.name} is excluded (known-broken: {_EXCLUDED_INSTANCE_SUBSTR})."
        )
        return 1

    # Preflight: the DTU CLI is always required; agent mode also needs the driver
    # key + the agent's own required host env.
    if not cli_available():
        _log(f"{BAD}: `amplifier-digital-twin` not on PATH")
        return 1

    agent: AgentSpec | None = None
    if args.mode == "agent":
        try:
            agent = load_agent(AGENTS_ROOT / args.agent)
        except SpecError as exc:
            _log(f"{BAD}: could not load agent {args.agent!r}: {exc}")
            return 1
        if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
            _log(f"{BAD}: ANTHROPIC_API_KEY not set on the host (AI User needs it)")
            return 1
        missing = verify_env(agent)
        if missing:
            _log(f"{BAD}: agent {agent.id} missing required host env: {', '.join(missing)}")
            return 1

    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser().resolve()
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out_dir = RUNS_ROOT / "swebench" / f"{stamp}-{args.mode}"

    _log(f"swebench: instance={task_dir.name}")
    _log(f"  mode={args.mode}" + (f" agent={args.agent}" if args.mode == "agent" else ""))
    _log(f"  out={out_dir}")

    passed, result = asyncio.run(_run_swebench_command(task_dir, args, agent, out_dir))

    _log("")
    if result:
        _render_swebench_result(result)
    if passed:
        _log(f"SWEBENCH {OK}: instance graded, verdict produced, DTU destroyed. Output: {out_dir}")
        return 0
    _log(f"SWEBENCH {BAD}: run did not produce a verdict (see logs above). Output: {out_dir}")
    return 1


async def _run_swebench_command(
    task_dir: Path, args: argparse.Namespace, agent: AgentSpec | None, out_dir: Path
) -> tuple[bool, dict[str, Any]]:
    """Set up drivers (agent mode) and run one swe-bench instance end to end."""
    ai_user: AIUser | None = None
    if args.mode == "agent":
        _log("setting up AI User (compose foundation + anthropic-opus-4-8)...")
        ai_user = AIUser()
        await ai_user.setup()
        _log("AI User ready")

    started = time.monotonic()
    try:
        result = await run_swe_bench_instance(
            task_dir=task_dir,
            mode=args.mode,
            out_dir=out_dir,
            agent=agent,
            ai_user=ai_user,
            cache_dir=args.cache_dir,
            launch_timeout_s=float(args.launch_timeout),
            grade_timeout_s=float(args.grade_timeout),
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure with context
        _log(f"{BAD}: swebench run raised: {type(exc).__name__}: {exc}")
        return False, {}
    _log(f"swebench run finished ({time.monotonic() - started:.0f}s wall)")
    # A verdict exists iff grading produced a grade dict.
    return ("grade" in result), result


def _render_swebench_result(result: dict[str, Any]) -> None:
    """Print the deterministic verdict as scannable gate evidence."""
    _log("--- swebench result ---")
    grade = result.get("grade") or {}
    f2p = grade.get("fail_to_pass") or {}
    p2p = grade.get("pass_to_pass") or {}
    _log(f"    instance:  {result.get('instance_id')}")
    _log(f"    repo:      {result.get('repo')}")
    _log(f"    mode:      {result.get('mode')}")
    _log(f"    resolved:  {grade.get('resolved')}  (overall_score={result.get('overall_score')})")
    _log(f"    fail_to_pass: {f2p.get('passed')}/{f2p.get('total')}  missing={f2p.get('missing')}")
    _log(f"    pass_to_pass: {p2p.get('passed')}/{p2p.get('total')}  missing={p2p.get('missing')}")
    if result.get("mode") == "agent":
        _log(f"    model_patch_bytes: {result.get('model_patch_bytes')}")
        _log(
            f"    ai_user_verdict:   {result.get('ai_user_verdict')} (timed_out={result.get('ai_user_timed_out')})"
        )


# ---------------------------------------------------------------------------
# The loader-driven `automationbench` subcommand (deterministic grading).
# ---------------------------------------------------------------------------

AB_TASKS_ROOT = TASKS_ROOT / "automation-bench"


def _resolve_automation_bench_dir(task: str) -> Path | None:
    """Resolve a task id OR a path to its automation-bench task dir.

    Accepts (a) a filesystem path to a task dir, (b) a directory name under
    `tasks/automation-bench/`, or (c) a `task:` value matched against the
    meta.yaml of any discovered automation-bench dir.
    """
    p = Path(task).expanduser()
    if p.is_dir() and is_automation_bench_dir(p):
        return p.resolve()
    direct = AB_TASKS_ROOT / task
    if direct.is_dir() and is_automation_bench_dir(direct):
        return direct.resolve()
    for task_dir in _discover_automation_bench_dirs(TASKS_ROOT):
        try:
            meta = AutomationBenchTaskLoader().read_meta(task_dir)
        except (ValueError, OSError):
            continue
        if str(meta.get("task")) == task:
            return task_dir.resolve()
    return None


def cmd_automationbench(args: argparse.Namespace) -> int:
    """Run one loader-driven automation-bench task (agent mode) and grade it.

    Loader-driven: synthesizes an Incus profile that installs AutomationBench and
    an `ab-tool` CLI, launches it, seeds the simulated world from the vendored
    task_info.json, installs + drives the agent-under-test via the AI User, then
    grades deterministically by assertions on the final world state. Always tears
    the DTU down. Exit 0 only when grading produced a verdict.
    """
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    task_dir = _resolve_automation_bench_dir(args.task)
    if task_dir is None:
        _log(f"{BAD}: could not resolve automation-bench task {args.task!r}")
        _log(f"    known tasks under {AB_TASKS_ROOT}:")
        for d in _discover_automation_bench_dirs(TASKS_ROOT):
            _log(f"      {d.name}")
        return 1

    if not cli_available():
        _log(f"{BAD}: `amplifier-digital-twin` not on PATH")
        return 1

    try:
        agent = load_agent(AGENTS_ROOT / args.agent)
    except SpecError as exc:
        _log(f"{BAD}: could not load agent {args.agent!r}: {exc}")
        return 1
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        _log(f"{BAD}: ANTHROPIC_API_KEY not set on the host (AI User needs it)")
        return 1
    missing = verify_env(agent)
    if missing:
        _log(f"{BAD}: agent {agent.id} missing required host env: {', '.join(missing)}")
        return 1

    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser().resolve()
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out_dir = RUNS_ROOT / "automationbench" / stamp

    _log(f"automationbench: task={task_dir.name}")
    _log(f"  agent={args.agent}")
    _log(f"  out={out_dir}")

    passed, result = asyncio.run(_run_automationbench_command(task_dir, args, agent, out_dir))

    _log("")
    if result:
        _render_automationbench_result(result)
    if passed:
        _log(
            f"AUTOMATIONBENCH {OK}: task graded, verdict produced, DTU destroyed. Output: {out_dir}"
        )
        return 0
    _log(
        f"AUTOMATIONBENCH {BAD}: run did not produce a verdict (see logs above). Output: {out_dir}"
    )
    return 1


async def _run_automationbench_command(
    task_dir: Path, args: argparse.Namespace, agent: AgentSpec, out_dir: Path
) -> tuple[bool, dict[str, Any]]:
    """Set up the AI User and run one automation-bench task end to end."""
    _log("setting up AI User (compose foundation + anthropic-opus-4-8)...")
    ai_user = AIUser()
    await ai_user.setup()
    _log("AI User ready")

    started = time.monotonic()
    try:
        result = await run_automation_bench_instance(
            task_dir=task_dir,
            out_dir=out_dir,
            agent=agent,
            ai_user=ai_user,
            launch_timeout_s=float(args.launch_timeout),
            grade_timeout_s=float(args.grade_timeout),
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure with context
        _log(f"{BAD}: automationbench run raised: {type(exc).__name__}: {exc}")
        return False, {}
    _log(f"automationbench run finished ({time.monotonic() - started:.0f}s wall)")
    return ("grade" in result), result


def _render_automationbench_result(result: dict[str, Any]) -> None:
    """Print the deterministic verdict as scannable gate evidence."""
    _log("--- automationbench result ---")
    _log(f"    task:      {result.get('task')}")
    _log(
        f"    partial_credit: {result.get('partial_credit')}  "
        f"(overall_score={result.get('overall_score')})"
    )
    _log(f"    task_completed_correctly: {result.get('task_completed_correctly')}")
    _log(
        f"    ai_user_verdict:   {result.get('ai_user_verdict')} "
        f"(timed_out={result.get('ai_user_timed_out')})"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval", description="Evaluation harness CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate all agent and task definitions.")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser(
        "run",
        help="Run a matrix of trials (agents x tasks x trials) in parallel via the scheduler.",
    )
    run.add_argument(
        "--agents",
        default=None,
        help="Comma-separated agent ids (default: all discovered agents).",
    )
    run.add_argument(
        "--tasks",
        default=None,
        help="Comma-separated task ids (default: all discovered tasks).",
    )
    run.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Trials per (agent, task) cell (default: 1).",
    )
    run.add_argument(
        "--max-parallel",
        type=int,
        default=4,
        help="Max concurrent trials (Semaphore cap; default: 4).",
    )
    run.add_argument(
        "--output-dir",
        default=None,
        help="Run output root (default: runs/matrix/<timestamp>).",
    )
    run.add_argument(
        "--list-agents",
        action="store_true",
        help="List available agent ids and exit.",
    )
    run.add_argument(
        "--list-tasks",
        action="store_true",
        help="List available task ids and exit.",
    )
    run.set_defaults(func=cmd_run)

    swebench = sub.add_parser(
        "swebench",
        help="Run one loader-driven swe-bench instance (gold or agent) and grade it.",
    )
    swebench.add_argument(
        "--instance",
        required=True,
        help="Instance id or path to a tasks/swe-bench-pro/instances/<id>/ dir.",
    )
    swebench.add_argument(
        "--mode",
        choices=["gold", "agent"],
        default="gold",
        help="gold: grade the dataset reference patch (default). agent: install + drive an agent.",
    )
    swebench.add_argument(
        "--agent",
        default="opencode-vanilla",
        help="Agent id to run in agent mode (a directory name under agents/).",
    )
    swebench.add_argument(
        "--cache-dir",
        default=None,
        help="Local clone of the official scaleapi repo (else shallow-cloned to a temp dir).",
    )
    swebench.add_argument(
        "--launch-timeout",
        type=float,
        default=3600.0,
        help="Seconds to allow for the DTU launch (provisioning builds the repo). Default 3600.",
    )
    swebench.add_argument(
        "--grade-timeout",
        type=float,
        default=3600.0,
        help="Seconds to allow for the in-DTU grading entry script. Default 3600.",
    )
    swebench.add_argument(
        "--output-dir",
        default=None,
        help="Output dir (default: runs/swebench/<timestamp>-<mode>).",
    )
    swebench.set_defaults(func=cmd_swebench)

    automationbench = sub.add_parser(
        "automationbench",
        help="Run one loader-driven automation-bench task (agent mode) and grade it.",
    )
    automationbench.add_argument(
        "--task",
        required=True,
        help="Task id (meta.yaml `task:`) or path to a tasks/automation-bench/<dir>/ dir.",
    )
    automationbench.add_argument(
        "--agent",
        default="opencode-vanilla",
        help="Agent id to run (a directory name under agents/).",
    )
    automationbench.add_argument(
        "--launch-timeout",
        type=float,
        default=3600.0,
        help="Seconds to allow for the DTU launch (provisioning installs AutomationBench). Default 3600.",
    )
    automationbench.add_argument(
        "--grade-timeout",
        type=float,
        default=600.0,
        help="Seconds to allow for the in-DTU `ab-tool grade` step. Default 600.",
    )
    automationbench.add_argument(
        "--output-dir",
        default=None,
        help="Output dir (default: runs/automationbench/<timestamp>).",
    )
    automationbench.set_defaults(func=cmd_automationbench)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
