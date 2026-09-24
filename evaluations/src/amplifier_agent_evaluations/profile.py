"""Run profiles and the task list they select."""

import copy
import fnmatch
from pathlib import Path
from typing import Any

from amplifier_agent_grader import rubric
import yaml

from amplifier_agent_evaluations import EVAL_ROOT, REPO_ROOT

INSTALLS = ("checkout", "github")
TASK_CONTENT = ("workspace", "grader-data")


class ProfileError(Exception):
    pass


def load_profile(path: Path, parallel: int | None = None) -> dict[str, Any]:
    """The run profile at `path` with `parallel` replacing its own when given, validated, and `output` made absolute."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}: a run profile is a mapping")
    profile = copy.deepcopy(raw)
    profile.setdefault("tasks", {})
    profile["tasks"].setdefault("include", ["*"])
    profile["tasks"].setdefault("exclude", [])
    profile.setdefault("trials", 1)
    if parallel is not None:
        profile["parallel"] = parallel
    validate_profile(profile, str(path))
    profile["output"] = str((REPO_ROOT / profile["output"]).resolve())
    return profile


def validate_profile(profile: dict[str, Any], where: str) -> None:
    errors: list[str] = []
    for key in ("name", "description", "output"):
        if not isinstance(profile.get(key), str) or not profile[key]:
            errors.append(f"{key} must be a non-empty string")
    if profile.get("install") not in INSTALLS:
        errors.append(f"install must be one of {', '.join(INSTALLS)}")
    for key in ("agent", "grader"):
        errors.extend(_selection_errors(profile.get(key), key))
    for key in ("include", "exclude"):
        value = profile["tasks"].get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f"tasks.{key} must be a list of globs")
    for key in ("trials", "parallel"):
        if not _positive_int(profile.get(key)):
            errors.append(f"{key} must be a positive integer")
    timeouts = profile.get("timeouts")
    if not isinstance(timeouts, dict):
        errors.append("timeouts must be a mapping")
    else:
        for key in ("launch_seconds", "task_seconds"):
            if not _positive_int(timeouts.get(key)):
                errors.append(f"timeouts.{key} must be a positive integer")
    if errors:
        raise ProfileError(f"{where}: " + "; ".join(errors))


def _selection_errors(value: Any, key: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{key} must be a mapping with provider and model"]
    return [f"{key}.{field} must be a non-empty string" for field in ("provider", "model") if not value.get(field)]


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def select_tasks(profile: dict[str, Any], tasks_root: Path = EVAL_ROOT / "tasks") -> list[dict[str, Any]]:
    """Every `tasks/**/task.yaml` whose id matches an include glob and no exclude glob.

    A task's id is its directory relative to `tasks_root`, for example `provider/openai`. Files under a task's
    `workspace/` or `grader-data/` are content, never tasks. Every selected task must have a valid `grader.yaml`;
    the error names every task that lacks one.
    """
    include = profile["tasks"]["include"]
    exclude = profile["tasks"]["exclude"]
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []
    for task_file in sorted(tasks_root.glob("**/task.yaml")):
        relative = task_file.parent.relative_to(tasks_root)
        if any(part in TASK_CONTENT for part in relative.parts):
            continue
        task_id = relative.as_posix()
        if not any(fnmatch.fnmatch(task_id, pattern) for pattern in include):
            continue
        if any(fnmatch.fnmatch(task_id, pattern) for pattern in exclude):
            continue
        task = yaml.safe_load(task_file.read_text())
        if not isinstance(task, dict) or not isinstance(task.get("turns"), list) or not task["turns"]:
            raise ProfileError(f"{task_file}: a task is a mapping with a non-empty turns list")
        task.setdefault("timeout_seconds", profile["timeouts"]["task_seconds"])
        grader_file = task_file.parent / "grader.yaml"
        if not grader_file.is_file():
            missing.append(task_id)
        else:
            try:
                rubric.load(grader_file)
            except rubric.RubricError as error:
                invalid.append(str(error))
        selected.append({"id": task_id, "dir": task_file.parent, "spec": task})
    problems = []
    if missing:
        problems.append(f"every task needs a grader.yaml; missing for: {', '.join(missing)}")
    problems.extend(invalid)
    if problems:
        raise ProfileError("\n".join(problems))
    return selected
