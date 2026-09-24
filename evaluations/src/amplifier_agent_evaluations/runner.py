"""Run one evaluation profile: preflight, snapshot or upstream sha, trials in parallel, summary."""

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

import yaml

from amplifier_agent_evaluations import preflight, provenance, snapshot, summarize
from amplifier_agent_evaluations.profile import ProfileError, load_profile, select_tasks
from amplifier_agent_evaluations.trial import Trial, locked_commit


def report(trial: str, stage: str, outcome: str, seconds: float | None) -> None:
    suffix = "" if seconds is None else f" {outcome} {seconds:.0f}s"
    print(f"[{trial}] {stage} ...{suffix}", flush=True)


async def run_trials(trials: list[Trial], parallel: int) -> list[dict]:
    gate = asyncio.Semaphore(parallel)

    async def one(trial: Trial) -> dict:
        async with gate:
            return await asyncio.to_thread(trial.run)

    return await asyncio.gather(*(one(trial) for trial in trials))


def run(
    path: Path,
    parallel: int | None = None,
) -> int:
    """Run the profile at `path`; 0 when every trial passed, 1 otherwise."""
    try:
        profile = load_profile(path, parallel)
        tasks = select_tasks(profile)
    except ProfileError as error:
        print(f"profile error: {error}", file=sys.stderr)
        return 1

    lines, missing = preflight.run(profile, tasks)
    print("\n".join(lines))
    if missing:
        print("preflight failed:", file=sys.stderr)
        for item in missing:
            print(f"  missing: {item}", file=sys.stderr)
        return 1
    print("preflight  ok", flush=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(profile["output"]) / f"{stamp}-{profile['name']}"
    run_dir.mkdir(parents=True)
    resolved = profile | {"tasks": profile["tasks"] | {"selected": [task["id"] for task in tasks]}}
    (run_dir / "run.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    print(f"run dir    {run_dir}", flush=True)

    if profile["install"] == "checkout":
        snap = snapshot.create()
        (run_dir / "snapshot.json").write_text(json.dumps(snap, indent=2))
        expected = snap["head"]
        print(f"snapshot   {expected}, {snap['files']} files", flush=True)
    else:
        expected = provenance.github_head()
        (run_dir / "upstream.json").write_text(
            json.dumps(
                {"url": provenance.UPSTREAM, "ref": f"refs/heads/{provenance.BRANCH}", "sha": expected}, indent=2
            )
        )
        print(f"upstream   {provenance.UPSTREAM} {provenance.BRANCH} {expected}", flush=True)

    grader_commit = locked_commit()
    print(f"grader     amplifier-agent {grader_commit} from grader/uv.lock", flush=True)

    trials = [
        Trial(
            task=task,
            n=n,
            profile=profile,
            run_dir=run_dir,
            expected=expected,
            grader_commit=grader_commit,
            report=report,
        )
        for task in tasks
        for n in range(1, profile["trials"] + 1)
    ]
    results = asyncio.run(run_trials(trials, profile["parallel"]))
    summary = summarize.summarize(run_dir)
    print(f"summary    {summary['counts']}, cost {summary['total_cost_usd']}")
    print(f"report     {run_dir / 'report.html'}")
    return 0 if results and all(result["status"] == "passed" for result in results) else 1
