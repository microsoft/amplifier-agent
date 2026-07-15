"""Static task loader: a thin pass-through over `eval.loaders.load_task`.

A static task ships everything on disk (task.yaml, grader.yaml, profile.yaml,
workspace/). This loader just loads the `TaskSpec` and points `profile_path` at
the on-disk `profile.yaml`, so it does NOT change existing static task behavior;
it only wraps it in the shared `LoadedTask` shape so the lifecycle can treat
static and loader-driven tasks uniformly.
"""

from __future__ import annotations

from pathlib import Path

from eval.loaders import load_task
from eval.task_loaders.base import LoadedTask


class StaticTaskLoader:
    """Load a static task dir (with a task.yaml) into a `LoadedTask`."""

    name = "static"

    def handles(self, task_dir: Path) -> bool:
        """A static task dir is any directory containing a `task.yaml`."""
        return (Path(task_dir) / "task.yaml").is_file()

    async def load(
        self,
        task_dir: Path,
        *,
        runtime_dir: Path | None = None,
        mode: str = "gold",
    ) -> LoadedTask:
        """Load the static task; its DTU profile is the on-disk profile.yaml.

        `runtime_dir` and `mode` are accepted to satisfy the `TaskLoader`
        protocol but ignored: a static task has no runtime materialization.
        """
        task = load_task(task_dir)
        return LoadedTask(
            task=task,
            profile_path=task.profile_path,
            grader_data_dir=None,
            loader=self.name,
        )


__all__ = ["StaticTaskLoader"]
