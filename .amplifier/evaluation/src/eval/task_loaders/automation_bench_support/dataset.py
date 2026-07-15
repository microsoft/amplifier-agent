"""Fetch a single AutomationBench task on the fly from the pinned upstream repo.

No AutomationBench task content is stored in this repo. AutomationBench data is
NOT a HuggingFace parquet -- it lives in-code as domain getters inside the
`zapier/AutomationBench` package (MIT). So the fetch source is the package
itself, pinned to a single commit.

Design (rate-limit safe): the AutomationBench repo is shallow-cloned AT MOST ONCE
per machine (keyed by the pinned commit) and reused for every task and every run.
`ensure_repo()` is idempotent -- once a valid checkout exists it never touches
GitHub again. `fetch_task()` then runs the existing dev-time extractor
(`extract_task.py`) in an isolated uv env with the local clone on PYTHONPATH,
capturing the task_info dict on stdout. That extraction reads local files only
(no network) once the clone exists.

Modeled on `eval.task_loaders.swe_bench_support` (`official_assets.ensure_repo`
for the idempotent shallow clone, `dataset.fetch_instance` for the fetch seam).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

AB_REPO_URL = "https://github.com/zapier/AutomationBench.git"
# Pinned commit (AutomationBench 1.0.5). Bump deliberately, never float to a branch.
AB_PIN = "eda214109cf891ebe8102ca826b87fb98911e103"

# The existing extractor, promoted from a dev-time tool to the runtime fetch entry
# point. Resolved relative to this file so no absolute user path is hardcoded:
#   this file: src/eval/task_loaders/automation_bench_support/dataset.py
#   parents[2] == src/eval, then automation_bench/extract_task.py
EXTRACT_TASK_PY = Path(__file__).resolve().parents[2] / "automation_bench" / "extract_task.py"

# In-process cache: same task within one process is extracted at most once. The
# extraction is deterministic and reads only the local clone.
_TASK_CACHE: dict[str, dict] = {}


def _has_valid_checkout(path: Path) -> bool:
    """True when ``path`` looks like a usable AutomationBench checkout."""
    return (path / "automationbench").is_dir()


def _clone_pinned(dest: Path) -> None:
    """Shallow-fetch AB_PIN into ``dest`` (already-empty dir).

    Preferred path pins the exact commit with a depth-1 fetch (mirrors the
    swe_bench official_assets shallow-clone intent). Some servers disallow
    fetch-by-sha; fall back to a full clone + checkout in that case.
    """
    dest.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        subprocess.run(
            ["git", "-C", str(dest), "remote", "add", "origin", AB_REPO_URL],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", AB_PIN],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"],
            check=True,
        )
    except subprocess.CalledProcessError:
        # Fallback: server refused fetch-by-sha. Full clone, then checkout the pin.
        logger.info("automation-bench: shallow fetch-by-sha failed, falling back to full clone")
        for child in dest.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        subprocess.run(["git", "clone", AB_REPO_URL, str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", AB_PIN], check=True)


def ensure_repo() -> Path:
    """Idempotently ensure a local AutomationBench checkout at AB_PIN; return its path.

    Cached in a stable per-pin dir (``<tmp>/automation-bench/<pin>``) so the clone
    happens AT MOST ONCE per machine and is reused for every task and every run.
    Concurrency-safe: clone into a temp sibling, then atomically ``os.replace`` it
    into place so a partial clone can never be observed as valid.
    """
    cache = Path(tempfile.gettempdir()) / "automation-bench" / AB_PIN
    if _has_valid_checkout(cache):
        logger.info("automation-bench: reusing cached checkout -> %s", cache)
        return cache

    cache.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".ab-{AB_PIN[:8]}-", dir=str(cache.parent)))
    try:
        logger.info("automation-bench: cloning %s@%s (first time)", AB_REPO_URL, AB_PIN)
        _clone_pinned(staging)
        if not _has_valid_checkout(staging):
            raise RuntimeError(
                f"AutomationBench checkout at {staging} is missing the "
                "`automationbench/` package after clone"
            )
        try:
            os.replace(staging, cache)
        except OSError:
            # Another process won the race and populated `cache` first (os.replace
            # onto a non-empty dir fails). If theirs is valid, use it.
            if _has_valid_checkout(cache):
                logger.info("automation-bench: another process cloned first -> %s", cache)
            else:
                raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return cache


def fetch_task(task_name: str, out_path: Path | None = None) -> dict:
    """Return the AutomationBench task_info dict for ``task_name`` (dotted name).

    Runs the extractor in an isolated uv env (python 3.13 + datasets + pydantic)
    with the local clone on PYTHONPATH so the host interpreter never imports
    `automationbench`. If ``out_path`` is given the dict is also written there
    (parents created) so downstream `ab-tool seed --info ...` keeps working.

    Args:
        task_name: Dotted AutomationBench task name, e.g. ``finance.timesheet_to_invoice``.
        out_path: Optional path to materialize the task_info.json.

    Returns:
        The task_info dict (``prompt``, ``info`` with ``zapier_tools`` /
        ``initial_state`` / ``assertions``). ``info`` is a nested dict, not a
        JSON string.

    Raises:
        RuntimeError: if the extraction subprocess fails or emits invalid JSON.
    """
    if task_name in _TASK_CACHE:
        task_info = _TASK_CACHE[task_name]
    else:
        repo = ensure_repo()
        env = {**os.environ, "PYTHONPATH": str(repo)}
        # Clone-once: GitHub was already hit (at most) by ensure_repo(); this step
        # only resolves PyPI deps (cached by uv) and reads the local clone.
        # If a `verifiers` ImportError ever surfaces at runtime, add "--with",
        # "verifiers" to this command.
        cmd = [
            "uv",
            "run",
            "--python",
            "3.13",
            "--with",
            "datasets",
            "--with",
            "pydantic",
            "python",
            str(EXTRACT_TASK_PY),
            task_name,
        ]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
            raise RuntimeError(
                f"automation-bench extraction failed for {task_name!r} "
                f"(exit {proc.returncode}):\n{stderr_tail}"
            )
        try:
            task_info = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            stdout_tail = "\n".join(proc.stdout.strip().splitlines()[-20:])
            raise RuntimeError(
                f"automation-bench extraction for {task_name!r} did not emit valid "
                f"JSON on stdout: {exc}\n--- stdout tail ---\n{stdout_tail}"
            ) from exc
        # Defensive: extract_task already normalizes this, but some rows pack
        # `info` as a JSON string upstream.
        if isinstance(task_info.get("info"), str):
            task_info["info"] = json.loads(task_info["info"])
        _TASK_CACHE[task_name] = task_info

    if out_path is not None:
        out_path = Path(out_path).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(task_info, indent=2), encoding="utf-8")

    return task_info


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "finance.timesheet_to_invoice"
    info = fetch_task(name)
    print("task keys:", sorted(info.keys()))
    print("prompt messages:", len(info.get("prompt", [])))
    inner = info.get("info", {})
    if isinstance(inner, dict):
        print("info keys:", sorted(inner.keys()))
