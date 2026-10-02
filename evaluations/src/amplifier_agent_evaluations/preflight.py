"""Everything a run needs from the host, checked before anything launches."""

import os
import shutil
import subprocess
from typing import Any

from amplifier_agent_evaluations import trial

PROVIDER_ENV: dict[str, list[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "github-copilot": ["COPILOT_AGENT_TOKEN", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"],
}


def requirements(profile: dict[str, Any], tasks: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """(what needs it, alternative env vars) for every credential the run needs."""
    needed: list[tuple[str, list[str]]] = []
    for role in ("agent", "grader"):
        provider = profile[role]["provider"]
        needed.append((f"{role} {provider}", PROVIDER_ENV.get(provider, [])))
    for task in tasks:
        spec = task["spec"]
        if "agent" in spec:
            provider = spec["agent"]["provider"]
            needed.append((f"task {task['id']} agent {provider}", PROVIDER_ENV.get(provider, [])))
        for entry in spec.get("requires_env") or []:
            alternatives = [entry] if isinstance(entry, str) else list(entry)
            needed.append((f"task {task['id']}", alternatives))
    return needed


# The task and turn keys the HTTP driver honors: the face takes tools, skills, approvals and sessions from its server
# host, never from a request, and the client holds the conversation.
HTTP_TASK_KEYS = {"name", "description", "surface", "agent", "env", "setup", "requires_env", "timeout_seconds", "turns"}
HTTP_TURN_KEYS = {"user", "images", "stream"}


def surface_problems(install: str, tasks: list[dict[str, Any]]) -> list[str]:
    """What stops a task's surface from running it: a missing container profile or driver, or task fields its
    driver cannot honor."""
    problems: list[str] = []
    for task in tasks:
        spec = task["spec"]
        surface = spec["surface"]
        who = f"task {task['id']} (surface {surface})"
        compose = trial.compose_file(surface, install)
        if not compose.is_file():
            problems.append(f"{who}: no container profile {compose}")
        driver = trial.DRIVER / trial.DRIVERS[surface][1]
        if not driver.is_file():
            problems.append(f"{who}: no driver {driver}")
        if surface == "typescript" and (spec.get("host") or spec.get("approvals") == "host"):
            problems.append(f"{who}: driver/hosts/ modules are Python; the TypeScript driver cannot load them")
        if surface == "http":
            extra = set(spec) - HTTP_TASK_KEYS
            if extra:
                problems.append(f"{who}: the HTTP face cannot honor {', '.join(sorted(extra))}")
            for index, turn in enumerate(spec["turns"]):
                extra = set(turn) - HTTP_TURN_KEYS if isinstance(turn, dict) else set()
                if extra:
                    problems.append(f"{who}: turn {index}: the HTTP face cannot honor {', '.join(sorted(extra))}")
    return problems


def missing_credentials(needed: list[tuple[str, list[str]]]) -> list[str]:
    missing: list[str] = []
    for who, alternatives in needed:
        if not alternatives:
            missing.append(f"{who}: no known credential variable for this provider")
        elif not any(os.environ.get(name) for name in alternatives):
            missing.append(f"{who}: set {' or '.join(alternatives)}")
    return sorted(set(missing))


def missing_host() -> list[str]:
    missing: list[str] = []
    if shutil.which("git") is None:
        missing.append("git is not on PATH")
    try:
        docker = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=60)
        if docker.returncode != 0:
            missing.append(f"`docker info` failed: {docker.stderr.strip()[-300:]}")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        missing.append(f"`docker info` failed: {error}")
    from dtu_lite import lib

    report = lib.check()
    for prerequisite in report.prerequisites:
        if not prerequisite.present:
            missing.append(f"dtu-lite {prerequisite.name}: {prerequisite.detail}. {prerequisite.remedy or ''}".strip())
    if not report.ok and not any(not p.present for p in report.prerequisites):
        missing.append("dtu-lite check reports the host is not ready")
    return missing


def run(profile: dict[str, Any], tasks: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(summary lines, missing items)."""
    needed = requirements(profile, tasks)
    summary = [
        f"run        {profile['name']} (install {profile['install']})",
        "surfaces   " + ", ".join(sorted({task["spec"]["surface"] for task in tasks})),
        f"agent      {profile['agent']['provider']}/{profile['agent']['model']}",
        f"grader     {profile['grader']['provider']}/{profile['grader']['model']}",
        f"tasks      {len(tasks)} x {profile['trials']} trials, parallel {profile['parallel']}: "
        + ", ".join(task["id"] for task in tasks),
        "credentials " + ", ".join(sorted({"|".join(alternatives) for _, alternatives in needed if alternatives})),
    ]
    missing = missing_credentials(needed) + surface_problems(profile["install"], tasks) + missing_host()
    if not tasks:
        missing.append("no task matches the profile's include/exclude globs")
    return summary, missing
