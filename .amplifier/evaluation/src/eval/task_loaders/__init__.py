"""Task loaders package: the pluggable task-materialization seam.

`task_loaders/base.py` defines how a task dir becomes a runnable spec + DTU
profile. `static` handles local dirs (task.yaml +
profile.yaml on disk); `swe_bench` fetches an instance from HuggingFace and
converts the official Dockerfile into an Incus profile at runtime. New
benchmarks add a loader here without touching the trial loop.

Public surface:

- `LoadedTask`, `TaskLoader`: the shared result + protocol (from `base`).
- `is_loader_driven(task_dir)` / `is_swe_bench_dir(task_dir)`: cheap, no-fetch
  routing checks the harness uses before launch.
- `StaticTaskLoader`, `SweBenchTaskLoader`: the two loaders.
- `loader_for(task_dir)`: return the loader that handles a task dir.
"""

from __future__ import annotations

from pathlib import Path

from eval.task_loaders.automation_bench import AutomationBenchTaskLoader
from eval.task_loaders.base import (
    LoadedTask,
    TaskLoader,
    is_automation_bench_dir,
    is_loader_driven,
    is_swe_bench_dir,
)
from eval.task_loaders.static import StaticTaskLoader
from eval.task_loaders.swe_bench import SweBenchTaskLoader


def loader_for(task_dir: Path, *, cache_dir: str | Path | None = None) -> TaskLoader:
    """Return the loader that handles `task_dir` (loader-driven if recognized, else static)."""
    task_dir = Path(task_dir)
    if is_automation_bench_dir(task_dir):
        return AutomationBenchTaskLoader()
    if is_swe_bench_dir(task_dir):
        return SweBenchTaskLoader(cache_dir=cache_dir)
    return StaticTaskLoader()


__all__ = [
    "LoadedTask",
    "TaskLoader",
    "StaticTaskLoader",
    "SweBenchTaskLoader",
    "AutomationBenchTaskLoader",
    "is_loader_driven",
    "is_swe_bench_dir",
    "is_automation_bench_dir",
    "loader_for",
]
