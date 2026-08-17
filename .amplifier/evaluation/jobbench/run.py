#!/usr/bin/env python3
"""JobBench harness entry point.

python run.py fetch --split easy
python run.py list-tasks --split easy
python run.py dtu-check
python run.py bake --agent amplifier-agent
python run.py run --agent amplifier-agent --task biostatisticians/task1 --split easy
python run.py run --agent all --all-tasks --split easy --max-parallel 4 --dry-run
python run.py grade runs/20260114T093012Z
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from jobbench import dataset, grading, images, matrix, orphans, scheduler, trial
from jobbench import dtu as dtu_mod
from jobbench.dataset import DatasetError, Task
from jobbench.dtu import DTUError
from jobbench.images import ImageError
from jobbench.matrix import MatrixError

# Reasoning models take an effort knob, not a temperature. Fixed rather than
# an argparse option -- letting it vary silently would make scores across
# runs incomparable for no offsetting benefit (see src/jobbench/judge.py).
JUDGE_REASONING_EFFORT = "medium"


def cmd_fetch(args: argparse.Namespace) -> int:
    dest = dataset.fetch(args.split, force=args.force)
    tasks = dataset.discover(args.split)
    total_rubrics = sum(t.rubric_count() for t in tasks)
    total_weight = sum(t.max_score() for t in tasks)
    print(f"split      {args.split}")
    print(f"location   {dest}")
    print(f"revision   {dataset.revision(args.split)}")
    print(f"tasks      {len(tasks)} across {len({t.occupation for t in tasks})} occupations")
    print(f"rubrics    {total_rubrics} ({total_weight} total weight)")
    return 0


def cmd_list_tasks(args: argparse.Namespace) -> int:
    tasks = dataset.discover(args.split)
    if args.occupation:
        tasks = [t for t in tasks if t.occupation == args.occupation]
        if not tasks:
            print(f"no tasks for occupation {args.occupation!r}", file=sys.stderr)
            return 1

    print(f"{'task':<52} {'rubrics':>7} {'weight':>7} {'inputs':>9}  search")
    for task in tasks:
        size_kb = task.input_bytes() / 1024
        size = f"{size_kb / 1024:.1f}M" if size_kb >= 1024 else f"{size_kb:.0f}K"
        print(
            f"{task.selector:<52} {task.rubric_count():>7} {task.max_score():>7} "
            f"{size:>9}  {'yes' if task.has_search_files else 'no'}"
        )
    print(f"\n{len(tasks)} tasks, {sum(t.max_score() for t in tasks)} total weight")
    return 0


def cmd_bake(args: argparse.Namespace) -> int:
    return asyncio.run(_bake(args.agent, force=args.force, base_only=args.base_only))


async def _bake(agent: str, *, force: bool, base_only: bool) -> int:
    """Bake jobbench-base, then the agent image on top of it.

    Each stage is skipped (fast) when its image already exists and `force`
    wasn't passed, so re-running this after the first successful bake is
    close to instant. Progress and elapsed time print per stage since a
    full bake (LibreOffice in particular) can take tens of minutes.
    """
    print(f"baking {images.BASE_ALIAS} from {images.BASE_PROFILE}...")
    try:
        base_result = await images.ensure_base_image(force=force)
    except ImageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if base_result.baked:
        print(f"{images.BASE_ALIAS}: baked in {base_result.elapsed_s:.0f}s")
    else:
        print(f"{images.BASE_ALIAS}: already exists, skipped ({base_result.elapsed_s:.1f}s)")

    if base_only:
        return 0

    alias = images.agent_alias(agent)
    profile = images.agent_bake_profile(agent)
    print(f"baking {alias} from {profile}...")
    try:
        agent_result = await images.ensure_agent_image(agent, force=force)
    except ImageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if agent_result.baked:
        print(f"{alias}: baked in {agent_result.elapsed_s:.0f}s")
    else:
        print(f"{alias}: already exists, skipped ({agent_result.elapsed_s:.1f}s)")

    return 0


def cmd_dtu_check(args: argparse.Namespace) -> int:
    return asyncio.run(_dtu_check())


async def _dtu_check() -> int:
    """Live end-to-end round trip through the DTU layer.

    Not a unit test -- this actually launches a container. It exists to catch
    the class of bug unit tests can't: a real CLI version whose JSON envelope
    shape drifted, or a real file-push/file-pull path that silently does
    nothing. Every step prints PASS/FAIL as it happens so a failure is
    diagnosable from the log alone.
    """

    def report(name: str, ok: bool, detail: str = "") -> bool:
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {name}"
        if detail:
            line += f" -- {detail}"
        print(line)
        return ok

    if not dtu_mod.cli_available():
        report("cli-available", False, f"`{dtu_mod.CLI}` is not on PATH")
        return 1
    report("cli-available", True)

    profile_path = Path(__file__).parent / "profiles" / "smoke.yaml"
    name = f"jb-smoke-{uuid.uuid4().hex[:8]}"
    print(f"launching {name} from {profile_path} (cold pulls can take several minutes)...")
    try:
        instance = await dtu_mod.DTU.launch(profile_path, name=name)
    except DTUError as exc:
        report("launch", False, str(exc))
        return 1
    report("launch", True, instance.id)

    ok = True
    try:
        result = await instance.exec_cmd(["bash", "-lc", "echo hello"])
        ok = report(
            "exec-basic",
            result.returncode == 0 and "hello" in result.stdout,
            f"rc={result.returncode} stdout={result.stdout!r}",
        )
        if not ok:
            return 1

        # CRITICAL: this is the load-bearing assertion for the whole harness.
        # The DTU CLI's `exec` reports the inner command's exit code inside a
        # JSON envelope on stdout and exits 0 itself; `_unwrap_exec_envelope`
        # is what recovers the real code. If this comes back 0 instead of 7,
        # the unwrap is not live and every fail-loud gate built on
        # `CommandResult.returncode` is silently checking the wrong layer.
        result = await instance.exec_cmd(["bash", "-lc", "exit 7"])
        ok = report(
            "exec-envelope-unwrap-exit-7",
            result.returncode == 7,
            f"rc={result.returncode} (expected 7)"
            if result.returncode == 7
            else (
                f"rc={result.returncode} -- BROKEN: inner exit code was not "
                "recovered from the JSON envelope; the harness is silently "
                "checking the CLI's own exit code instead"
            ),
        )
        if not ok:
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pushed_dir = tmp_path / "push-me"
            pushed_dir.mkdir()
            (pushed_dir / "marker.txt").write_text("pushed\n", encoding="utf-8")
            await instance.file_push(pushed_dir, "/workspace/")
            result = await instance.exec_cmd(["bash", "-lc", "cat /workspace/push-me/marker.txt"])
            ok = report(
                "file-push-roundtrip",
                result.returncode == 0 and "pushed" in result.stdout,
                f"rc={result.returncode} stdout={result.stdout!r}",
            )
            if not ok:
                return 1

            pull_content = "pulled-content\n"
            write_cmd = f"printf %s {shlex.quote(pull_content)} > /workspace/pull-me.txt"
            result = await instance.exec_cmd(["bash", "-lc", write_cmd])
            if not report(
                "write-file-in-container",
                result.returncode == 0,
                f"rc={result.returncode} stderr={result.stderr!r}",
            ):
                return 1

            pulled_path = tmp_path / "pulled.txt"
            await instance.file_pull("/workspace/pull-me.txt", pulled_path)
            pulled = pulled_path.read_text(encoding="utf-8") if pulled_path.is_file() else None
            ok = report(
                "file-pull-roundtrip",
                pulled == pull_content,
                f"content={pulled!r} (expected {pull_content!r})",
            )
            if not ok:
                return 1

        print("all dtu-check steps passed")
        return 0
    finally:
        print(f"destroying {instance.id}...")
        await instance.destroy()


def _utc_stamp() -> str:
    """Filesystem-safe UTC timestamp for a run directory, e.g. 20260114T093012Z."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _print_dry_run(
    agent_list: list[str],
    tasks: list[Task],
    pairs: list[matrix.Pair],
    *,
    split: str,
    max_parallel: int,
) -> None:
    """Everything `--dry-run` promises: the matrix, the trial count, and a
    cost estimate. Launches nothing -- no image check, no orphan sweep.
    """
    print(f"split        {split}  (revision {dataset.revision(split)})")
    print(f"agents       {len(agent_list)}  {agent_list}")
    print(f"tasks        {len(tasks)}")
    print(f"trials       {len(pairs)}  ({len(agent_list)} agents x {len(tasks)} tasks)")
    print(f"max_parallel {max_parallel}")
    print()
    print("matrix:")
    for pair in pairs:
        print(f"  {pair.agent:<24} {pair.task.selector}")
    print()
    low, mean, high = matrix.estimate_cost(len(pairs))
    print(
        "cost estimate (derived from prior-run cost_usd observations -- an ESTIMATE, not a quote):"
    )
    print(f"  ${low:,.2f} - ${high:,.2f}  (mean ~${mean:,.2f})")
    print()
    print("dry run -- nothing launched")


def _fmt_num(value: float | str | None) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    if value is None:
        return "-"
    return str(value)


def _print_summary(
    outcomes: list[scheduler.PairOutcome], *, started_at: str, finished_at: str
) -> None:
    """One line per trial (this run's own bookkeeping, straight from each
    trial.json -- not the cross-task aggregator), plus totals.
    """
    print()
    print("results:")
    for outcome in outcomes:
        score = (
            f"{outcome.total_score}/{outcome.max_score}" if outcome.total_score is not None else "-"
        )
        warn_flag = "WARN" if outcome.has_warnings else ""
        print(
            f"  [{outcome.pair.label}] status={outcome.status:<14} "
            f"agent_run_s={_fmt_num(outcome.agent_run_s):<8} "
            f"cost_usd={_fmt_num(outcome.cost_usd):<8} score={score:<9} {warn_flag}"
        )

    completed = sum(1 for o in outcomes if not o.skipped and o.status == "completed")
    skipped = sum(1 for o in outcomes if o.skipped)
    failed = sum(1 for o in outcomes if not o.skipped and o.status != "completed")

    # The per-trial lines above print "-" for a missing cost; the total has to
    # be just as honest. A whole arm can report no cost telemetry BY
    # CONSTRUCTION (opencode-amplifier pulls no telemetry, so every cost field
    # is the string "not_available"), which would otherwise make the headline
    # cost silently understate the run by one full arm with nothing saying so.
    costed = [o.cost_usd for o in outcomes if isinstance(o.cost_usd, int | float)]
    total_cost = sum(costed)
    uncosted = len(outcomes) - len(costed)
    cost_note = (
        f" ({uncosted} of {len(outcomes)} trials reported no cost telemetry)" if uncosted else ""
    )

    elapsed_s = (
        datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)
    ).total_seconds()

    print()
    print(
        f"totals       {completed} completed, {failed} failed, {skipped} skipped  "
        f"elapsed={elapsed_s:.0f}s  cost=${total_cost:,.2f}{cost_note}"
    )


# Every key `_record_judge_attribution` owns. Rewritten (or removed) wholesale
# on each grading pass so a failed re-grade can never leave the PREVIOUS
# judge's run-level claim standing over scores it did not produce.
_JUDGE_ATTRIBUTION_KEYS = (
    "judge_model",
    "judge_reasoning_effort",
    "grading_complete",
    "attempted_judge_model",
    "attempted_judge_reasoning_effort",
    "ungraded_trials",
)


def _record_judge_attribution(manifest: dict, *, judge_model: str, ungraded: int) -> None:
    """Record which judge graded this run, honestly.

    A top-level `judge_model` is a claim that this judge produced EVERY score
    in the run, so it is written only when `ungraded` is 0 -- i.e. every trial
    in the matrix got a score from this judge on this pass. Otherwise the
    judge is recorded as `attempted_judge_model` alongside `grading_complete:
    false` and the count of trials it produced no score for, so a partial
    grading pass is visible rather than indistinguishable from a clean one.

    Per-score provenance is not this function's job: each trial.json carries
    its own `judge_model` next to its score (see grading.grade_and_record).
    """
    for key in _JUDGE_ATTRIBUTION_KEYS:
        manifest.pop(key, None)
    manifest["grading_complete"] = ungraded == 0
    if ungraded == 0:
        manifest["judge_model"] = judge_model
        manifest["judge_reasoning_effort"] = JUDGE_REASONING_EFFORT
    else:
        manifest["attempted_judge_model"] = judge_model
        manifest["attempted_judge_reasoning_effort"] = JUDGE_REASONING_EFFORT
        manifest["ungraded_trials"] = ungraded


def cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    """Run an (agent, task) matrix -- one pair by default, many under
    --agent/--task expansion -- landing each trial's artifacts under
    --output-dir exactly as a single-pair run always has.

    Everything one trial itself does (launch, seed, execute, pull, destroy)
    lives in jobbench.trial; concurrency, skip-existing, and per-pair
    bookkeeping live in jobbench.scheduler. This function is the CLI-facing
    shell: resolve and validate the WHOLE matrix before anything launches,
    check every agent's image is baked, lay out the run directory, sweep
    orphaned DTUs before and after, and print progress/summary.
    """
    try:
        agent_list = matrix.resolve_agent_names(args.agent)
        tasks = matrix.resolve_tasks(split=args.split, raw=args.task, all_tasks=args.all_tasks)
    except MatrixError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # --bundle is an amplifier-foundation-specific concept (which bundle.md
    # `amplifier run` composes). Rejecting it for every other agent here,
    # rather than letting it fall through to a TypeError from the adapter's
    # own __init__, keeps the error message about the flag, not about
    # Python's constructor mismatch. Checked against the WHOLE agent set
    # up front, same as the agent/task validation above.
    if args.bundle is not None:
        non_foundation = [a for a in agent_list if a != "amplifier-foundation"]
        if non_foundation:
            print(
                f"error: --bundle is only supported by amplifier-foundation, not {non_foundation}",
                file=sys.stderr,
            )
            return 2

    def agent_kwargs_for(agent: str) -> dict[str, str]:
        if agent == "amplifier-foundation" and args.bundle is not None:
            return {"bundle": args.bundle}
        return {}

    pairs = matrix.build_matrix(agent_list, tasks)

    if args.dry_run:
        _print_dry_run(agent_list, tasks, pairs, split=args.split, max_parallel=args.max_parallel)
        return 0

    # Every image must be baked before the first trial launches -- a missing
    # image for the 3rd of 4 agents must fail instantly, not after the first
    # two agents' tasks have already run for hours.
    missing = [
        (agent, images.agent_alias(agent))
        for agent in agent_list
        if not await images.image_exists(images.agent_alias(agent))
    ]
    if missing:
        for agent, alias in missing:
            print(
                f"error: image {alias!r} is not baked; run "
                f"`python run.py bake --agent {agent}` first",
                file=sys.stderr,
            )
        return 1

    output_dir = Path(args.output_dir)
    run_id = args.run_id or _utc_stamp()
    run_root = output_dir / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    print(f"run          {run_id}" + ("  (reusing existing run dir)" if args.run_id else ""))
    print(f"agents       {agent_list}")
    print(f"split        {args.split}  (revision {dataset.revision(args.split)})")
    print(f"matrix       {len(pairs)} trial(s)  ({len(agent_list)} agents x {len(tasks)} tasks)")
    print(f"max_parallel {args.max_parallel}")
    print(f"model        {args.model}")
    print(f"timeout      {args.timeout:.0f}s")
    print(f"output dir   {run_root}")
    if args.skip_existing:
        print("skip-existing on")
    print()

    started_at = datetime.now(UTC).isoformat()

    # Sweep BEFORE the matrix starts -- none of OUR trials is running yet, so
    # any jb-prefixed instance the CLI reports looks like a leak from a prior
    # hard-killed run. It cannot tell that apart from a container a peer
    # harness process on this host is using, which is what --no-orphan-sweep
    # is for. See jobbench.orphans.
    if args.orphan_sweep:
        reaped_before = await orphans.sweep_orphans()
        if reaped_before:
            print(
                f"orphan sweep (pre-run): destroyed {len(reaped_before)} "
                f"leaked DTU(s): {reaped_before}"
            )
            print()
    else:
        print("orphan sweep disabled (--no-orphan-sweep); leaked DTUs must be reaped by hand")
        print()

    judge_api_base, judge_api_key = (None, None)
    if args.grade:
        judge_api_base, judge_api_key = grading.resolve_credentials(
            args.judge_api_base, args.judge_api_key
        )

    outcomes = await scheduler.run_matrix(
        pairs,
        run_root,
        model=args.model,
        timeout_s=args.timeout,
        max_parallel=args.max_parallel,
        agent_kwargs_for=agent_kwargs_for,
        skip_existing=args.skip_existing,
        grade=args.grade,
        judge_model=args.judge_model,
        judge_api_base=judge_api_base,
        judge_api_key=judge_api_key,
        judge_max_workers=args.judge_max_workers,
        judge_timeout_per_rubric=args.judge_timeout_per_rubric,
    )

    finished_at = datetime.now(UTC).isoformat()

    # Sweep AFTER the matrix finishes -- every one of our own trials has
    # already run its own destroy() by this point, so anything still
    # jb-prefixed here is a leak from a trial that never reached `finally`
    # (or, if a peer harness process is running on this host, one of ITS live
    # containers -- hence --no-orphan-sweep).
    if args.orphan_sweep:
        reaped_after = await orphans.sweep_orphans()
        if reaped_after:
            print()
            print(
                f"orphan sweep (post-run): destroyed {len(reaped_after)} "
                f"leaked DTU(s): {reaped_after}"
            )

    manifest = {
        "run_id": run_id,
        "agents": agent_list,
        "tasks": [t.selector for t in tasks],
        "matrix_size": len(pairs),
        "max_parallel": args.max_parallel,
        "model": args.model,
        "split": args.split,
        "dataset_revision": dataset.revision(args.split),
        "image_aliases": {a: images.agent_alias(a) for a in agent_list},
        "timeout_s": args.timeout,
        "network_policy": {"allow_external": True},
        "skip_existing": args.skip_existing,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    if len(agent_list) == 1:
        # Back-compat with `cmd_grade`, which keys off a single "agent"
        # string -- a single-agent manifest (any number of tasks) stays
        # re-gradable through the existing `grade` subcommand unchanged.
        manifest["agent"] = agent_list[0]
    if "amplifier-foundation" in agent_list:
        # Record what actually ran, not just what was overridden -- a run
        # left at the default is otherwise invisible in the manifest, and
        # the default is a moving `@main` ref that can change between runs.
        from jobbench.agents.amplifier_foundation import DEFAULT_BUNDLE

        manifest["bundle"] = args.bundle or DEFAULT_BUNDLE
    if args.grade:
        # A skipped pair was NOT graded on this pass -- it keeps whatever
        # score (and judge) a previous pass gave it, which may not be this
        # judge -- so it counts as ungraded here just like a grading failure
        # does. Either one means this judge did not produce every score in
        # the run, and the manifest must not claim otherwise.
        ungraded = sum(1 for o in outcomes if o.skipped or not o.graded_ok)
        _record_judge_attribution(manifest, judge_model=args.judge_model, ungraded=ungraded)
    (run_root / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    _print_summary(outcomes, started_at=started_at, finished_at=finished_at)

    # A skipped pair contributes neither a pass nor a fail; only pairs that
    # actually ran THIS invocation determine the exit code.
    ran = [o for o in outcomes if not o.skipped]
    all_ok = all(o.status == "completed" and o.graded_ok for o in ran)
    return 0 if all_ok else 1


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade every trial in an already-completed run, without re-running the agent.

    Reads run-manifest.json to find the run's agent/split/tasks, then grades
    each trial directory in place -- the same code path `run --grade` uses,
    so a re-graded run and a graded-at-run-time run are scored identically.

    Only supports single-agent run directories (the shape `run` has always
    written for one agent, regardless of task count). A multi-agent matrix
    run is already graded per-trial as part of `run` itself (see
    jobbench.scheduler) -- there is nothing left for this command to do for
    those runs, so it fails loudly rather than guessing which agent's trials
    to re-grade.
    """
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file():
        print(f"error: no run-manifest.json under {run_dir}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if "agent" not in manifest:
        print(
            f"error: {run_dir} is a multi-agent matrix run (agents={manifest.get('agents')}); "
            "`grade` only supports single-agent run directories -- each trial in a matrix run "
            "is already graded during `run` itself",
            file=sys.stderr,
        )
        return 2

    agent = manifest["agent"]
    split = manifest.get("split", "main")
    api_base, api_key = grading.resolve_credentials(args.api_base, args.api_key)

    print(f"run         {run_dir}")
    print(f"agent       {agent}")
    print(f"judge model {args.judge_model}  (reasoning_effort={JUDGE_REASONING_EFFORT})")
    print()

    ungraded = 0
    all_ok = True
    for task_selector in manifest.get("tasks", []):
        task = dataset.resolve(split, [task_selector])[0]
        trial_dir = run_dir / agent / task.id
        if not trial_dir.is_dir():
            print(
                f"warning: no trial directory for {task_selector} at {trial_dir}", file=sys.stderr
            )
            ungraded += 1
            all_ok = False
            continue
        ok = grading.grade_and_record(
            trial_dir,
            task,
            agent=agent,
            judge_model=args.judge_model,
            api_base=api_base,
            api_key=api_key,
            max_workers=args.max_workers,
            timeout_per_rubric=args.timeout_per_rubric,
        )
        ungraded += 0 if ok else 1
        all_ok = all_ok and ok

    # Only claim this judge graded the run when it actually graded every
    # trial. A trial whose grading failed now holds no score at all (see
    # grading.grade_and_record), so an unconditional claim here would attribute
    # the REMAINING trials' scores -- some possibly from a previous judge -- to
    # this one.
    _record_judge_attribution(manifest, judge_model=args.judge_model, ungraded=ungraded)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return 0 if all_ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_split(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--split",
            default="main",
            choices=sorted(dataset.SPLITS),
            help="which split to operate on (default: main)",
        )

    p_fetch = sub.add_parser("fetch", help="download a split from Hugging Face")
    add_split(p_fetch)
    p_fetch.add_argument("--force", action="store_true", help="re-download even if present")
    p_fetch.set_defaults(func=cmd_fetch)

    p_list = sub.add_parser("list-tasks", help="list tasks in a downloaded split")
    add_split(p_list)
    p_list.add_argument("--occupation", help="restrict to one occupation")
    p_list.set_defaults(func=cmd_list_tasks)

    p_dtu_check = sub.add_parser(
        "dtu-check", help="live end-to-end DTU round trip (launches a real container)"
    )
    p_dtu_check.set_defaults(func=cmd_dtu_check)

    p_bake = sub.add_parser(
        "bake", help="bake the golden Incus images (jobbench-base, then an agent)"
    )
    p_bake.add_argument("--agent", required=True, help="agent name, e.g. amplifier-agent")
    p_bake.add_argument("--force", action="store_true", help="re-bake even if the image exists")
    p_bake.add_argument(
        "--base-only", action="store_true", help="bake jobbench-base only, skip the agent image"
    )
    p_bake.set_defaults(func=cmd_bake)

    p_run = sub.add_parser(
        "run", help="run an (agent, task) matrix in fresh DTUs, bounded by --max-parallel"
    )
    p_run.add_argument(
        "--agent",
        action="append",
        metavar="NAME",
        required=True,
        help=(
            "agent name, e.g. amplifier-agent (repeatable: --agent a --agent b; comma-separated "
            "ok: --agent a,b); 'all' expands to every registered agent"
        ),
    )
    p_run.add_argument(
        "--task",
        action="append",
        metavar="SELECTOR",
        help=(
            "task selector, e.g. biostatisticians/task1 (repeatable, comma-separated ok); "
            "required unless --all-tasks is given"
        ),
    )
    p_run.add_argument(
        "--all-tasks",
        action="store_true",
        help="run every task in --split instead of listing --task selectors",
    )
    add_split(p_run)
    p_run.add_argument(
        "--model", default="claude-sonnet-5", help="model to configure the agent with"
    )
    p_run.add_argument(
        "--bundle",
        default=None,
        help=(
            "override the bundle amplifier-foundation runs (default: its own anchors "
            "default); rejected for every other agent, which has no bundle concept"
        ),
    )
    p_run.add_argument(
        "--timeout",
        type=float,
        default=trial.DEFAULT_TIMEOUT_S,
        help="agent wall-clock timeout, seconds",
    )
    p_run.add_argument("--output-dir", default="runs", help="root directory for run artifacts")
    p_run.add_argument(
        "--max-parallel",
        type=int,
        default=2,
        help="max concurrent trials (default: 2; each trial launches a full container)",
    )
    p_run.add_argument(
        "--run-id",
        default=None,
        help=(
            "reuse/extend an existing run directory under --output-dir (default: a fresh "
            "UTC timestamp) -- the way to top up a partial sweep with --skip-existing"
        ),
    )
    p_run.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip (agent, task) pairs whose trial.json already has status=completed",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the matrix, trial count, and a cost estimate, then exit -- launches nothing",
    )
    p_run.add_argument(
        "--no-orphan-sweep",
        dest="orphan_sweep",
        action="store_false",
        default=True,
        help=(
            "skip the pre-run and post-run sweeps of leaked jb- DTUs; use this when another "
            "harness process is running on the same host, since the sweep cannot tell that "
            "process's live containers from leaks and would destroy them (leaked DTUs then "
            "have to be reaped by hand)"
        ),
    )
    _add_judge_args(p_run, flag_prefix="judge-", dest_prefix="judge_")
    p_run.add_argument(
        "--grade",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="grade each trial's deliverables against its task rubric after it finishes (default: on)",
    )
    p_run.set_defaults(func=cmd_run)

    p_grade = sub.add_parser(
        "grade", help="grade every trial in an already-completed run, without re-running the agent"
    )
    p_grade.add_argument("run_dir", help="run directory, e.g. runs/20260114T093012Z")
    _add_judge_args(p_grade, flag_prefix="", dest_prefix="")
    p_grade.set_defaults(func=cmd_grade)

    return parser


def _add_judge_args(p: argparse.ArgumentParser, *, flag_prefix: str, dest_prefix: str) -> None:
    """Judge configuration flags shared by `run --grade` and `grade`.

    `run` namespaces these under `--judge-*` / `args.judge_*` so they read
    clearly next to the agent's own `--model`; `grade`, where judging is the
    only thing happening, uses the bare names.
    """
    p.add_argument(
        f"--{flag_prefix}model",
        # Always `judge_model`, even for `run` where the agent's own `--model`
        # already owns the bare `model` dest -- this is never that model.
        dest="judge_model",
        default=grading.DEFAULT_JUDGE_MODEL,
        help=f"judge model (default: {grading.DEFAULT_JUDGE_MODEL})",
    )
    p.add_argument(
        f"--{flag_prefix}api-base",
        dest=f"{dest_prefix}api_base",
        default=None,
        help="judge API base URL (default: $OPENAI_BASE_URL)",
    )
    p.add_argument(
        f"--{flag_prefix}api-key",
        dest=f"{dest_prefix}api_key",
        default=None,
        help="judge API key (default: $OPENAI_API_KEY)",
    )
    p.add_argument(
        f"--{flag_prefix}max-workers",
        dest=f"{dest_prefix}max_workers",
        type=int,
        default=grading.DEFAULT_MAX_WORKERS,
        help=f"parallel rubric judge calls (default: {grading.DEFAULT_MAX_WORKERS})",
    )
    p.add_argument(
        f"--{flag_prefix}timeout-per-rubric",
        dest=f"{dest_prefix}timeout_per_rubric",
        type=int,
        default=grading.DEFAULT_TIMEOUT_PER_RUBRIC,
        help=f"per-rubric judge call timeout, seconds (default: {grading.DEFAULT_TIMEOUT_PER_RUBRIC})",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
