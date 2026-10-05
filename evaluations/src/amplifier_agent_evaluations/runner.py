"""Run one evaluation profile: preflight, snapshot or upstream sha, image bake, trials in parallel, summary."""

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

import yaml

from amplifier_agent_evaluations import bake, preflight, provenance, snapshot, summarize, universe
from amplifier_agent_evaluations.profile import ProfileError, load_profile, select_tasks
from amplifier_agent_evaluations.trial import Trial, locked_commit


def report(trial: str, stage: str, outcome: str, seconds: float | None) -> None:
    suffix = "" if seconds is None else f" {outcome} {seconds:.0f}s"
    print(f"[{trial}] {stage} ...{suffix}", flush=True)


def expected_identities(install: str, surfaces: set[str], run_dir: Path) -> dict[str, str]:
    """Each selected surface's expected installed identity, recorded in snapshot.json or upstream.json.

    The releases API is asked only when a TypeScript task is selected.
    """
    if install == "checkout":
        snap = snapshot.create()
        (run_dir / "snapshot.json").write_text(json.dumps(snap, indent=2))
        print(f"snapshot   {snap['head']}, {snap['files']} files", flush=True)
        return dict.fromkeys(surfaces, snap["head"])
    sha = provenance.github_tag_commit()
    expected = {
        surface: provenance.github_release_digest() if surface == "typescript" else sha for surface in sorted(surfaces)
    }
    upstream = {"url": provenance.UPSTREAM, "ref": f"refs/tags/{provenance.TAG}", "sha": sha, "expected": expected}
    (run_dir / "upstream.json").write_text(json.dumps(upstream, indent=2))
    print(f"upstream   {provenance.UPSTREAM} {provenance.TAG} {sha}", flush=True)
    if "typescript" in expected:
        print(f"upstream   {provenance.TYPESCRIPT_ASSET} {expected['typescript']}", flush=True)
    return expected


async def run_trials(trials: list[Trial], parallel: int) -> list[dict]:
    gate = asyncio.Semaphore(parallel)

    async def one(trial: Trial) -> dict:
        async with gate:
            return await asyncio.to_thread(trial.run)

    return await asyncio.gather(*(one(trial) for trial in trials))


def run(
    path: Path,
    parallel: int | None = None,
    rebake: bool = False,
    no_bake: bool = False,
    keep_images: int = bake.KEEP_IMAGES,
) -> int:
    """Run the profile at `path`; 0 when every trial passed, 1 otherwise.

    Trials run baked images unless `no_bake`, when each installs for itself. `rebake` bakes even when an image with
    the same key exists. After the bakes, each image repository the run used keeps its `keep_images` newest images
    and the grader cache keeps as many keys; 0 keeps everything.
    """
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

    surfaces = {task["spec"]["surface"] for task in tasks}
    expected = expected_identities(profile["install"], surfaces, run_dir)

    grader_commit = locked_commit()
    print(f"grader     amplifier-agent {grader_commit} from grader/uv.lock", flush=True)

    images: dict[str, dict[str, Any]] = {}
    grader_cache: Path | None = None
    if not no_bake:
        try:
            baked = bake.ensure(
                surfaces,
                profile["install"],
                expected,
                run_dir,
                profile["timeouts"]["launch_seconds"],
                grader_commit,
                rebake,
                keep_images,
            )
        except (bake.BakeError, universe.DigitalTwinUniverseError) as error:
            print(f"bake failed: {error}", file=sys.stderr)
            return 1
        images, grader_cache = baked["images"], Path(baked["grader_cache"])

    trials = [
        Trial(
            task=task,
            n=n,
            profile=profile,
            run_dir=run_dir,
            expected=expected[task["spec"]["surface"]],
            grader_commit=grader_commit,
            report=report,
            bake=images.get(task["spec"]["surface"]),
            grader_cache=grader_cache,
        )
        for task in tasks
        for n in range(1, profile["trials"] + 1)
    ]
    results = asyncio.run(run_trials(trials, profile["parallel"]))
    summary = summarize.summarize(run_dir)
    print(f"summary    {summary['counts']}, cost {summary['total_cost_usd']}")
    print(f"report     {run_dir / 'report.html'}")
    return 0 if results and all(result["status"] == "passed" for result in results) else 1
