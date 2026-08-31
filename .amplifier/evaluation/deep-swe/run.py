#!/usr/bin/env python3
"""Runner for the deep-swe benchmark.

Thin wrapper over the `pier` CLI: resolves the pinned deep-swe task checkout,
validates the agent/task selection, and shells out to `pier run` once per agent.

Task data is cloned at runtime into a cache dir OUTSIDE this repo and is never
redistributed here.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from deepswe_agents import AGENTS, LOCAL_SOURCE_AGENTS
from deepswe_agents.providers import API_KEY_VAR, provider_family

DEEP_SWE_REPO = "https://github.com/datacurve-ai/deep-swe"
DEEP_SWE_SHA = "435ee89ec2f2e2289f33b0da4f992f0b7b7266b9"

PIER_GIT_SHA = "0daf53d3599e58c4506cf0bcff5e12c77dc282d2"
PIER_INSTALL_CMD = (
    f"uv tool install --force git+https://github.com/datacurve-ai/pier@{PIER_GIT_SHA}"
)

DEFAULT_MODEL = "anthropic/claude-sonnet-5"
HERE = Path(__file__).parent

# Multiplier applied to each task's `[agent] timeout_sec` (5400s for the tasks
# seen so far). At 1.0 a harder task has little headroom, and a timeout produces
# a 0 that looks like a capability result rather than a truncation. 1.5 buys
# headroom without materially changing the ceiling on a well-behaved run, since
# a fast agent simply finishes earlier.
DEFAULT_AGENT_TIMEOUT_MULTIPLIER = 1.5

# Per-agent constructor kwarg -> upstream repo whose HEAD gets resolved to a
# commit SHA at run start. Every arm that installs from a moving branch belongs
# here; opencode-vanilla does not, because it already pins an installer VERSION.
#
# WHY THIS EXISTS. A multi-task run takes hours. Installing from `@main` means
# upstream can move between the first task and the last, so tasks get graded
# against different agent code and the comparison quietly stops being valid.
# Resolving once per run and passing the SHA to every trial makes a run
# internally consistent and, via run-manifest.json, reproducible afterwards.
PINNABLE_REFS: dict[str, dict[str, str]] = {
    "amplifier-agent": {
        "amplifier_agent_ref": "https://github.com/microsoft/amplifier-agent",
    },
    "amplifier-foundation": {
        "amplifier_ref": "https://github.com/microsoft/amplifier",
        "anchors_ref": (
            "https://github.com/microsoft/amplifier-foundation"
            "#subdirectory=bundles/anchors/bundle.md"
        ),
    },
}

# One timestamped directory per invocation, holding one job dir per agent.
# Everything a run produces -- pier job output, agent session trees, extracted
# trajectories, metrics -- lands under here so a run is a single self-contained
# artifact that can be archived or deleted as a unit.
RUN_TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")


def default_results_root() -> Path:
    root = os.environ.get("DEEP_SWE_RESULTS_DIR")
    if root:
        return Path(root).expanduser()
    # deep-swe/ -> evaluation/ -> .amplifier/ -> amplifier-agent/ -> workspace root
    return HERE.parents[3] / "evaluation_results"


# ----------------------------------------------------------------------
# Task checkout
# ----------------------------------------------------------------------


def default_tasks_dir() -> Path:
    root = os.environ.get("DEEP_SWE_CACHE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".cache" / "deep-swe"
    return base / DEEP_SWE_SHA


def ensure_tasks(tasks_dir: Path) -> Path:
    """Clone deep-swe at the pinned SHA if not already present. Idempotent."""
    checkout = tasks_dir
    tasks = checkout / "tasks"
    if tasks.is_dir():
        head = _git(checkout, "rev-parse", "HEAD", check=False)
        if head == DEEP_SWE_SHA:
            return tasks
        print(f"Task checkout at {checkout} is at {head}, re-fetching {DEEP_SWE_SHA}...")

    checkout.mkdir(parents=True, exist_ok=True)
    if not (checkout / ".git").is_dir():
        _git(checkout, "init", "-q")
        _git(checkout, "remote", "add", "origin", DEEP_SWE_REPO)
    print(f"Fetching deep-swe@{DEEP_SWE_SHA[:8]} into {checkout} ...")
    _git(checkout, "fetch", "-q", "--depth", "1", "origin", DEEP_SWE_SHA)
    _git(checkout, "checkout", "-q", "FETCH_HEAD")
    if not tasks.is_dir():
        die(f"deep-swe checkout at {checkout} has no tasks/ directory.")
    return tasks


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} failed in {cwd}:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def list_task_names(tasks: Path) -> list[str]:
    return sorted(p.name for p in tasks.iterdir() if p.is_dir())


# ----------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------


def preflight(model: str, require_docker: bool = True) -> None:
    pier = shutil.which("pier")
    if not pier:
        die(f"`pier` is not on PATH. Install it with:\n  {PIER_INSTALL_CMD}")
    check_pier_is_git_build(pier)

    # Only the family actually under test is required. Demanding an Anthropic
    # key for an OpenAI run would force a dummy value whose only effect is to
    # satisfy this check -- a gate that has stopped gating anything.
    # `--model` may carry a `<provider>/` prefix; the family is derived from the
    # bare id, exactly as every adapter derives it.
    bare_model = model.split("/", 1)[-1]
    key_var = API_KEY_VAR[provider_family(bare_model)]
    if not os.environ.get(key_var):
        die(f"{key_var} is not set (required for model {model!r}). Export it before running.")

    if require_docker:
        if not shutil.which("docker"):
            die("`docker` is not on PATH. deep-swe tasks run in Docker containers.")
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            die("Docker daemon is not reachable. Start Docker and retry.")


def check_pier_is_git_build(pier_path: str) -> None:
    """PyPI datacurve-pier==0.3.0 lacks [[verifier.collect]] hooks.

    Without them `model.patch` is never produced and EVERY task silently scores
    zero. Both builds self-report version "0.3.0", so probe for the feature.
    """
    venv_python = Path(pier_path).resolve().parent / "python"
    proc = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import importlib.util;s=importlib.util.find_spec('pier');print(s.origin or '')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    origin = proc.stdout.strip()
    if proc.returncode != 0 or not origin:
        die(
            "Could not locate the installed `pier` package to verify the build.\n"
            f"Reinstall the known-good build:\n  {PIER_INSTALL_CMD}"
        )

    trial_py = Path(origin).parent / "trial" / "trial.py"
    if not trial_py.exists() or "_run_collect_hooks" not in trial_py.read_text(encoding="utf-8"):
        die(
            "Installed `pier` is the PyPI build: it has no [[verifier.collect]] hook\n"
            "support, so model.patch is never produced and every task silently\n"
            "scores 0. Install the pinned git build:\n"
            f"  {PIER_INSTALL_CMD}"
        )


# ----------------------------------------------------------------------
# Command construction
# ----------------------------------------------------------------------


def resolve_task_selection(args: argparse.Namespace, available: list[str]) -> list[str]:
    """Resolve the task list ONCE, host-side, before any agent runs.

    WHY NOT `--n-tasks`/`--sample-seed`: those are pier flags, and this runner
    invokes pier once per agent in a separate process. Delegating the sampling
    would mean each arm draws its own subset, and any difference in pier's
    sampling -- version, task-dir ordering, implementation change -- yields arms
    graded on DIFFERENT tasks while every summary still lines them up
    side by side. That failure is invisible in the output.

    Resolving here and emitting explicit `--include-task-name` for every arm
    makes the identical-subset property structural rather than assumed, and
    `run-manifest.json` records exactly which tasks ran.
    """
    if args.tasks:
        missing = [t for t in args.tasks if t not in set(available)]
        if missing:
            die(
                f"unknown task(s): {', '.join(missing)}.\n"
                "  Run `python run.py --list-tasks` to see valid ids."
            )
        return list(args.tasks)

    if args.n_tasks > len(available):
        die(f"-n {args.n_tasks} exceeds the {len(available)} tasks in the checkout.")

    # Sorted input + explicit seed: the same seed always yields the same subset,
    # on any machine, independent of filesystem ordering.
    return sorted(random.Random(args.seed).sample(sorted(available), args.n_tasks))


def resolve_pins(agents: list[str], *, enabled: bool) -> dict[str, dict[str, str]]:
    """Resolve each pinnable arm's moving ref to a commit SHA, once per run.

    Returns {agent: {kwarg: "git+<url>@<sha>[#subdirectory=...]"}}. An arm with
    nothing to pin (opencode-vanilla) is simply absent.

    Never fabricates: if `git ls-remote` fails for a ref, that ref is left
    unpinned and the reason is printed, so the manifest shows a moving ref
    rather than a SHA that was never verified.
    """
    if not enabled:
        return {}
    pins: dict[str, dict[str, str]] = {}
    for agent in agents:
        for kwarg, url in PINNABLE_REFS.get(agent, {}).items():
            base, _, fragment = url.partition("#")
            proc = subprocess.run(
                ["git", "ls-remote", base, "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            sha = proc.stdout.split("\t", 1)[0].strip() if proc.returncode == 0 else ""
            if len(sha) != 40:
                print(
                    f"  WARNING: could not resolve {base} to a commit "
                    f"({(proc.stderr or 'no SHA in output').strip()[:120]}); "
                    f"{agent}.{kwarg} stays on its moving default.",
                    file=sys.stderr,
                )
                continue
            suffix = f"#{fragment}" if fragment else ""
            pins.setdefault(agent, {})[kwarg] = f"git+{base}@{sha}{suffix}"
    return pins


def write_run_manifest(
    args: argparse.Namespace,
    agents: list[str],
    tasks: list[str],
    pins: dict[str, dict[str, str]],
) -> None:
    """Record what this run actually pinned and selected, next to its results.

    This is the file that makes a run reproducible and auditable after the fact:
    the task subset, the seed that produced it, the deep-swe checkout SHA, and
    the exact agent refs each arm installed.
    """
    manifest = {
        "run_timestamp": RUN_TIMESTAMP,
        "deep_swe_sha": DEEP_SWE_SHA,
        "pier_sha": PIER_GIT_SHA,
        "model": args.model,
        "agents": agents,
        "tasks": tasks,
        "task_selection": {
            "mode": "explicit" if args.tasks else "sample",
            "n_tasks": args.n_tasks,
            "seed": args.seed,
            "identical_across_agents": True,
        },
        "agent_timeout_multiplier": args.agent_timeout_multiplier,
        "n_concurrent": args.n_concurrent,
        "pins": pins or None,
        "local_source": args.local_source,
    }
    path = args.jobs_dir / "run-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {path}")


def build_command(args: argparse.Namespace, agent: str, tasks: Path) -> list[str]:
    cmd = [
        "pier",
        "run",
        "--agent-import-path",
        AGENTS[agent],
        "--model",
        args.model,
        "--path",
        str(tasks),
        "--jobs-dir",
        str(args.jobs_dir),
        "--job-name",
        f"{args.job_name}-{agent}",
        "-y",
    ]
    # Every arm gets the SAME explicit task list, resolved once in main(). pier's
    # own --n-tasks/--sample-seed are deliberately never used: see
    # resolve_task_selection.
    for task in args.selected_tasks:
        cmd += ["--include-task-name", task]
    if args.agent_timeout_multiplier is not None:
        cmd += ["--agent-timeout-multiplier", str(args.agent_timeout_multiplier)]
    if args.n_concurrent is not None:
        cmd += ["--n-concurrent", str(args.n_concurrent)]
    for kwarg, ref in (args.pins or {}).get(agent, {}).items():
        cmd += ["--ak", f"{kwarg}={ref}"]
    if args.local_source:
        cmd += ["--ak", f"local_source={args.local_source}"]
    cmd += args.pier_arg or []
    return cmd


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------


# Substrings that mean the agent process died rather than merely scored badly.
# pier reports such trials as n_errored_trials=0 when the process still exited 0,
# so "solved nothing" is otherwise indistinguishable from "died on turn 3".
CRASH_MARKERS = ("[amplifier-agent error:",)

# Markers matched only at the START of a line. `Error:` is too generic to search
# for anywhere in a line -- an agent legitimately printing compiler output would
# trip it -- but opencode's fatal message always begins one.
CRASH_LINE_PREFIXES = ("Error:",)


def _read_json(path: Path) -> dict | None:
    """Read a JSON object, or None if absent/unreadable/not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - summary must never crash the run
        return None
    return data if isinstance(data, dict) else None


def find_crash_markers(trial_dir: Path) -> list[str]:
    """Return the distinct agent.log lines that indicate an early crash."""
    log = trial_dir / "agent" / "agent.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - missing/unreadable log is not a crash signal
        return []
    hits: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        matched = any(marker in line for marker in CRASH_MARKERS) or stripped.startswith(
            CRASH_LINE_PREFIXES
        )
        if matched:
            trimmed = stripped[:200]
            if trimmed not in hits:
                hits.append(trimmed)
    return hits


def _num(value: object) -> float | None:
    """Return value as a float if it is a real number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


# ----------------------------------------------------------------------
# Trial timings (one measurement: the adapter's monotonic agent clock)
# ----------------------------------------------------------------------
#
# The ONLY duration reported here is `agent_run_s`, measured by the adapter with
# `time.monotonic()`. pier derives its own durations from the wall clock, which
# can step backward under NTP correction; `time.monotonic()` cannot. Those
# pier timings are still written to the trial result.json, they are simply not
# reported here -- do not reinstate them, not even as a fallback.


def trial_timings(result: dict) -> dict[str, object]:
    """Derive the agent duration and the timeout flag."""
    exc_info = result.get("exception_info")
    exc_type = exc_info.get("exception_type") if isinstance(exc_info, dict) else None
    agent_result = result.get("agent_result")
    metadata = agent_result.get("metadata") if isinstance(agent_result, dict) else None
    agent_run_s = _num(metadata.get("agent_run_s")) if isinstance(metadata, dict) else None
    return {
        # The only duration we report. Adapter-measured with time.monotonic().
        # pier's wall-clock-derived durations (agent_execution,
        # environment_setup, verifier, ...) are deliberately not read here.
        "agent_run_s": agent_run_s,
        "timed_out": exc_type == "AgentTimeoutError",
    }


def _agent_field(timings: dict[str, object]) -> str:
    """`agent=1037s`, from the adapter's monotonic measurement.

    There is no fallback: a trial without that measurement (any trial predating
    it) reports `agent=n/a` rather than a number derived from a clock we do not
    trust.
    """
    agent_s = timings.get("agent_run_s")
    if not isinstance(agent_s, (int, float)):
        return "agent=n/a"
    return f"agent={agent_s:.0f}s"


def summarize_trial(trial_dir: Path) -> tuple[str, float | None]:
    """Return (summary line, cost_usd or None) for one trial directory."""
    result = _read_json(trial_dir / "result.json") or {}
    reward = _read_json(trial_dir / "verifier" / "reward.json") or {}
    # Fall back to the in-result copy when verifier/reward.json is absent.
    if not reward:
        reward = (result.get("verifier_result") or {}).get("rewards") or {}

    name = result.get("task_name") or trial_dir.name
    agent_result = result.get("agent_result") or {}
    cost = _num(agent_result.get("cost_usd"))
    # Every token processed: fresh input + cache + output. This is additive and
    # double-counts nothing because metrics.py normalizes `input_tokens` to
    # fresh-only in BOTH branches (the amplifier sources natively fold
    # cache_read into it; see `parse_events`). Matches metrics.json's
    # `total_tokens`.
    token_parts = [
        _num(agent_result.get(key))
        for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
    ]
    tokens = None if all(t is None for t in token_parts) else sum(t or 0 for t in token_parts)

    f2p_passed = _fmt(_num(reward.get("f2p_passed")), "g")
    f2p_total = _fmt(_num(reward.get("f2p_total")), "g")
    fields = [
        f"reward={_fmt(_num(reward.get('reward')), 'g')}",
        f"f2p={f2p_passed}/{f2p_total}",
        f"partial={_fmt(_num(reward.get('partial')), '.3f')}",
        "cost=n/a" if cost is None else f"cost=${cost:.2f}",
        f"tokens={_fmt(tokens, ',.0f')}",
    ]
    timings = trial_timings(result)
    fields.append(_agent_field(timings))

    exc_info = result.get("exception_info") or {}
    exc_type = exc_info.get("exception_type")
    if timings.get("timed_out"):
        fields.append("TIMEOUT")
    if exc_type:
        fields.append(f"EXC: {exc_type}")
    line = f"  {name:<50} {'  '.join(fields)}"
    return line, cost


def summarize(jobs_dir: Path, job_names: list[str]) -> None:
    """Print per-trial results read from the trial dirs.

    Deliberately NOT read from the job-level result.json: pier writes that file
    with `exclude={"trial_results"}` at every call site, so the key is never
    present. The per-trial `verifier/reward.json` and `result.json` are the real
    source of truth.

    Never raises -- this runs after a paid run and must not destroy the output.
    """
    try:
        print("\n" + "=" * 64)
        print("SUMMARY  (reward is binary; partial is the dev signal)")
        print("=" * 64)
        for job_name in job_names:
            job_dir = jobs_dir / job_name
            print(f"\n{job_name}")
            if not job_dir.is_dir():
                print("  no job dir (run errored or was cancelled)")
                continue
            trials = sorted(
                p
                for p in job_dir.iterdir()
                if p.is_dir() and ((p / "result.json").exists() or (p / "verifier").is_dir())
            )
            if not trials:
                print("  no trials recorded")
                continue
            total_cost = 0.0
            saw_cost = False
            for trial_dir in trials:
                line, cost = summarize_trial(trial_dir)
                print(line)
                if cost is not None:
                    saw_cost = True
                    total_cost += cost
                for hit in find_crash_markers(trial_dir):
                    print(f"  !! CRASHED: {trial_dir.name}: {hit}")
            total = f"${total_cost:.2f}" if saw_cost else "n/a"
            print(f"  {'TOTAL COST':<50} {total}")
    except Exception as exc:  # noqa: BLE001 - summary must never crash the run
        print(f"could not summarize results: {exc}", file=sys.stderr)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Run Amplifier agents against the deep-swe benchmark via pier.",
        epilog=(
            "Extra raw pier args: repeat --pier-arg.\n  e.g. --pier-arg --max-retries --pier-arg 2"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--agents", help="comma-separated agent short names")
    parser.add_argument("--tasks", help="comma-separated deep-swe task ids")
    parser.add_argument(
        "-n",
        "--n-tasks",
        type=int,
        help="run a deterministic sample of N tasks (same N for every agent)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="sample seed used with -n (default: 0). The same seed always "
        "selects the same tasks, so two runs are directly comparable.",
    )
    parser.add_argument(
        "--n-concurrent",
        type=int,
        default=None,
        help="trials pier runs in parallel within one agent (pier --n-concurrent). "
        "Unset means pier's default (sequential). Each trial is a container with "
        "its own cpu/memory budget from the task's task.toml.",
    )
    parser.add_argument(
        "--agent-timeout-multiplier",
        type=float,
        default=DEFAULT_AGENT_TIMEOUT_MULTIPLIER,
        help=f"multiplier on each task's agent timeout_sec "
        f"(default: {DEFAULT_AGENT_TIMEOUT_MULTIPLIER}). A timed-out trial scores 0, "
        f"which is indistinguishable from a capability failure, so headroom matters.",
    )
    parser.add_argument(
        "--no-pin",
        action="store_true",
        help="do NOT resolve agent refs to commit SHAs; install from moving "
        "branches instead. Faster to start, but a multi-hour run can span "
        "upstream commits and grade different tasks against different code.",
    )
    parser.add_argument(
        "--local-source", help="path to a local checkout to install instead of the pinned ref"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=default_results_root() / RUN_TIMESTAMP,
        help="output dir for this run (default: <workspace>/evaluation_results/<timestamp>)",
    )
    parser.add_argument("--tasks-dir", type=Path, default=None, help="deep-swe checkout cache dir")
    # The results dir is already timestamped, so the job name only has to
    # separate agents within one run. pier raises FileExistsError on collision.
    parser.add_argument("--job-name", default="deepswe")
    parser.add_argument("--list-agents", action="store_true")
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print pier commands, do not run")
    parser.add_argument(
        "--pier-arg", action="append", help="raw arg forwarded to pier (repeatable)"
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.list_agents:
        for name, path in AGENTS.items():
            local = " (supports --local-source)" if name in LOCAL_SOURCE_AGENTS else ""
            print(f"{name:<28} {path}{local}")
        return 0

    tasks_dir = args.tasks_dir or default_tasks_dir()

    if args.list_tasks:
        for name in list_task_names(ensure_tasks(tasks_dir)):
            print(name)
        return 0

    if not args.agents:
        die(f"--agents is required. Valid names: {', '.join(AGENTS)}")
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    unknown = [a for a in agents if a not in AGENTS]
    if unknown:
        die(f"unknown agent(s): {', '.join(unknown)}. Valid names: {', '.join(AGENTS)}")

    args.tasks = [t.strip() for t in (args.tasks or "").split(",") if t.strip()]

    if not args.tasks and args.n_tasks is None:
        die(
            "a task selection is required -- refusing to run the full 113-task matrix.\n"
            f"  113 tasks x {len(agents)} agent(s) is hours-to-days of runtime and\n"
            "  hundreds of dollars in API spend. Use --tasks <id,...> or -n <count>."
        )

    if args.local_source:
        bad = [a for a in agents if a not in LOCAL_SOURCE_AGENTS]
        if bad:
            die(
                f"--local-source is not supported by: {', '.join(bad)}.\n"
                f"  Supported: {', '.join(sorted(LOCAL_SOURCE_AGENTS))}"
            )
        src = Path(args.local_source).expanduser().resolve()
        if not src.is_dir():
            die(f"--local-source path does not exist: {src}")
        args.local_source = str(src)

    preflight(args.model, require_docker=not args.dry_run)
    tasks = ensure_tasks(tasks_dir)

    # Resolve ONCE, then hand the identical explicit list to every agent.
    args.selected_tasks = resolve_task_selection(args, list_task_names(tasks))
    print(f"\ntask selection ({len(args.selected_tasks)} task(s), identical for every agent):")
    for name in args.selected_tasks:
        print(f"  {name}")
    if args.n_tasks is not None:
        print(f"  (deterministic sample, seed={args.seed})")

    args.pins = resolve_pins(agents, enabled=not args.no_pin)
    if args.pins:
        print("\nagent refs pinned for this run:")
        for agent, refs in args.pins.items():
            for kwarg, ref in refs.items():
                print(f"  {agent}.{kwarg} = {ref}")
    elif not args.no_pin:
        print("\nno agent refs required pinning (or none could be resolved).")

    args.jobs_dir = Path(args.jobs_dir).expanduser().resolve()
    # A dry run prints commands only; creating the timestamped results dir here
    # would litter evaluation_results/ with empty dirs.
    if not args.dry_run:
        args.jobs_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        write_run_manifest(args, agents, args.selected_tasks, args.pins)

    job_names: list[str] = []
    failures = 0
    for agent in agents:
        cmd = build_command(args, agent, tasks)
        job_names.append(f"{args.job_name}-{agent}")
        print("\n" + "=" * 64)
        print(f"AGENT: {agent}")
        print("=" * 64)
        print(" ".join(cmd))
        if args.dry_run:
            continue
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            failures += 1
            print(f"pier run failed for {agent} (exit {proc.returncode})", file=sys.stderr)

    if args.dry_run:
        return 0

    summarize(args.jobs_dir, job_names)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
