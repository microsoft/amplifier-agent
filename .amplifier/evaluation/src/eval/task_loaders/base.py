"""Task loaders: the pluggable seam that turns a task dir into a runnable task.

A task dir becomes a runnable trial in one of two ways:

- `static`: the task dir ships everything on disk (task.yaml, grader.yaml,
  profile.yaml, workspace/). The loader is a thin pass-through over
  `eval.loaders.load_task`; the DTU profile is the on-disk `profile.yaml`.
- `swe_bench`: the task dir ships only a `meta.yaml` naming a HuggingFace
  instance. The loader fetches the instance row + official Docker assets AT
  RUNTIME, converts the Dockerfiles into a DTU profile, and synthesizes the
  deterministic grader config (test lists) from the fetched data.

Both loaders return a `LoadedTask`: a runnable `TaskSpec`, the path to the DTU
profile to launch, and (for loader-driven tasks) a `grader_data_dir` the
deterministic grader reads its runtime config + assets from. New benchmarks add
a loader here without touching the trial loop.

The distinction the lifecycle cares about: a STATIC task's profile is a fixed
file, so it can launch directly. A LOADER-DRIVEN task has NO static profile.yaml;
the loader must run first (fetch + convert) to materialize the profile, scenario,
and grader config. `is_loader_driven(task_dir)` is how the harness tells them
apart before launch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from eval.schema import TaskSpec


@dataclass
class LoadedTask:
    """The runtime materialization of a task dir, ready for a trial.

    `task` is the runnable spec (scenario, timeout, deliverable, grader_path).
    `profile_path` is the DTU profile to launch: an on-disk file for static
    tasks, a generated file for loader-driven tasks. `grader_data_dir` is the
    host directory a deterministic grader reads its runtime config and pushed
    assets from (None for static tasks whose grader needs no runtime data).
    `extras` carries loader-specific data the lifecycle needs (e.g. the swe-bench
    gold patch and per-instance metadata) without widening this shared shape.
    """

    task: TaskSpec
    profile_path: Path
    grader_data_dir: Path | None = None
    loader: str = "static"
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TaskLoader(Protocol):
    """The pluggable task-loading contract.

    `handles(task_dir)` returns True when this loader recognizes the task dir's
    layout. `load(...)` materializes the runnable `LoadedTask`; for loader-driven
    tasks this performs the fetch + convert and writes generated artifacts under
    `runtime_dir`. `mode` is loader-specific (swe_bench uses "gold" / "agent");
    static ignores it.
    """

    name: str

    def handles(self, task_dir: Path) -> bool:
        """True if this loader recognizes the on-disk layout at `task_dir`."""
        ...

    async def load(
        self,
        task_dir: Path,
        *,
        runtime_dir: Path,
        mode: str = "gold",
    ) -> LoadedTask:
        """Materialize a runnable `LoadedTask` from `task_dir`.

        Args:
            task_dir: The task directory to load.
            runtime_dir: Host directory for any generated artifacts (a generated
                profile.yaml, the grader data dir, fetched assets). Created if
                absent. Static loaders may ignore it.
            mode: Loader-specific mode. swe_bench uses "gold" / "agent".
        """
        ...


def is_swe_bench_dir(task_dir: Path) -> bool:
    """True if `task_dir` is a swe-bench instance dir (meta.yaml, no task.yaml).

    A swe-bench instance ships a `meta.yaml` carrying `instance_id` + `dataset`
    and, unlike a static task, NO `task.yaml`. This is the cheap, no-fetch check
    the harness uses to route a task dir to the swe_bench loader and to validate
    it shallowly (without hitting HuggingFace).
    """
    import yaml

    meta = task_dir / "meta.yaml"
    if not meta.is_file() or (task_dir / "task.yaml").is_file():
        return False
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and "instance_id" in data and "dataset" in data


def is_automation_bench_dir(task_dir: Path) -> bool:
    """True if `task_dir` is an automation-bench task dir (meta.yaml, no task.yaml).

    An automation-bench task ships a `meta.yaml` carrying `benchmark:
    automation-bench` (+ `task` + `timeout`) and, unlike a static task, NO
    `task.yaml`. This is the cheap, no-import check the harness uses to route a
    task dir to the automation_bench loader and to validate it shallowly. The
    `benchmark` key distinguishes it from a swe-bench instance dir (which keys on
    `instance_id` + `dataset`).
    """
    import yaml

    meta = task_dir / "meta.yaml"
    if not meta.is_file() or (task_dir / "task.yaml").is_file():
        return False
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and str(data.get("benchmark", "")).strip() == "automation-bench"


def is_loader_driven(task_dir: Path) -> bool:
    """True if `task_dir` needs a loader to materialize its profile at runtime.

    Static tasks (with a `task.yaml` + on-disk `profile.yaml`) are NOT
    loader-driven. Today swe-bench instances and automation-bench tasks are
    loader-driven; the check is factored out so the lifecycle branch reads
    intent, not layout details.
    """
    return is_swe_bench_dir(task_dir) or is_automation_bench_dir(task_dir)


__all__ = [
    "LoadedTask",
    "TaskLoader",
    "is_loader_driven",
    "is_swe_bench_dir",
    "is_automation_bench_dir",
]
