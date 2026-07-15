"""Load agents and tasks from disk into typed specs.

Each loader is pure I/O plus schema validation: no DTU, no LLM, no side effects
beyond reading files. Downstream code (the trial loop, the scheduler) never has
to touch the on-disk layout convention.

On-disk layout (see the plan's target structure):

    agents/<id>/
        meta.yaml        # id, description, model (statically configured)
        install.yaml     # requires.env + setup_cmds
        invocation.md    # how to drive the agent
        extract.yaml     # agent-owned extraction/metrics hints

    tasks/<group>/<id>/
        task.yaml        # id, description, scenario, timeout, deliverable,
                         # grader + profile references
        grader.yaml      # grader type + config
        profile.yaml     # DTU profile
        workspace/       # files seeded into /workspace
        seed/            # optional prior sessions planted pre-agent

Tasks are grouped (custom/, benchmark/, swe-bench-pro/), so `discover_tasks`
walks recursively for any directory that contains a `task.yaml`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from eval.schema import (
    AgentInstall,
    AgentSpec,
    TaskDeliverable,
    TaskSpec,
)


class SpecError(ValueError):
    """Raised when a definition is missing required fields or files."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SpecError(f"missing required file: {path}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SpecError(f"{path}: expected a YAML mapping at the top level")
    return data


def _require_str(data: dict[str, Any], key: str, source: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{source}: `{key}:` must be a non-empty string")
    return value


def _require_str_list(value: Any, key: str, source: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{source}: `{key}:` must be a list of strings")
    return list(value)


def load_agent(agent_dir: Path | str) -> AgentSpec:
    """Load an agent from a directory like `agents/<id>/`.

    Required files: `meta.yaml`, `install.yaml`, `invocation.md`, `extract.yaml`.
    Required `meta.yaml` fields: `id`, `description`, `model`.
    Required `install.yaml`: a non-empty `setup_cmds` list.
    """
    d = Path(agent_dir).expanduser().resolve()
    if not d.is_dir():
        raise SpecError(f"agent directory not found: {d}")

    meta_path = d / "meta.yaml"
    install_path = d / "install.yaml"
    invocation_path = d / "invocation.md"
    extract_path = d / "extract.yaml"

    meta = _read_yaml(meta_path)
    install_data = _read_yaml(install_path)
    extract = _read_yaml(extract_path)

    if not invocation_path.is_file():
        raise SpecError(f"missing required file: {invocation_path}")
    invocation_md = invocation_path.read_text(encoding="utf-8")
    if not invocation_md.strip():
        raise SpecError(f"{invocation_path}: invocation.md must not be empty")

    agent_id = _require_str(meta, "id", meta_path)
    description = _require_str(meta, "description", meta_path)
    model = _require_str(meta, "model", meta_path)

    setup_cmds = _require_str_list(install_data.get("setup_cmds"), "setup_cmds", install_path)
    if not setup_cmds:
        raise SpecError(f"{install_path}: `setup_cmds:` must not be empty")
    required_env = _require_str_list(
        (install_data.get("requires") or {}).get("env", []),
        "requires.env",
        install_path,
    )

    return AgentSpec(
        id=agent_id,
        dir=d,
        description=description,
        model=model,
        install=AgentInstall(setup_cmds=setup_cmds, required_env=required_env),
        invocation_md_path=invocation_path,
        invocation_md=invocation_md,
        extract_path=extract_path,
        extract=extract,
        meta=meta,
    )


def load_task(task_dir: Path | str) -> TaskSpec:
    """Load a task from a directory like `tasks/<group>/<id>/`.

    Required file: `task.yaml`. Required `task.yaml` fields: `id`, `scenario`,
    and a `deliverable` mapping with a `path`. `grader` and `profile` reference
    sibling files (default `grader.yaml` / `profile.yaml`) that must exist. A
    `workspace/` directory is required. A `seed/` directory is optional.
    """
    d = Path(task_dir).expanduser().resolve()
    if not d.is_dir():
        raise SpecError(f"task directory not found: {d}")

    task_path = d / "task.yaml"
    task_data = _read_yaml(task_path)

    task_id = _require_str(task_data, "id", task_path)
    description = str(task_data.get("description", "")).strip()
    scenario = _require_str(task_data, "scenario", task_path)
    timeout_s = int(task_data.get("timeout", 3600))

    deliverable_data = task_data.get("deliverable")
    if not isinstance(deliverable_data, dict):
        raise SpecError(f"{task_path}: `deliverable:` must be a mapping with a `path`")
    deliverable_path = _require_str(deliverable_data, "path", task_path)
    deliverable = TaskDeliverable(
        path=deliverable_path,
        description=str(deliverable_data.get("description", "")).strip(),
    )

    grader_ref = str(task_data.get("grader", "grader.yaml"))
    profile_ref = str(task_data.get("profile", "profile.yaml"))
    grader_path = (d / grader_ref).resolve()
    profile_path = (d / profile_ref).resolve()
    if not grader_path.is_file():
        raise SpecError(f"{task_path}: grader file not found: {grader_path}")
    if not profile_path.is_file():
        raise SpecError(f"{task_path}: profile file not found: {profile_path}")

    workspace_dir = d / "workspace"
    if not workspace_dir.is_dir():
        raise SpecError(f"{task_path}: required `workspace/` directory not found: {workspace_dir}")

    seed_dir = d / "seed"

    return TaskSpec(
        id=task_id,
        dir=d,
        description=description,
        scenario=scenario,
        timeout_s=timeout_s,
        deliverable=deliverable,
        grader_path=grader_path,
        profile_path=profile_path,
        workspace_dir=workspace_dir,
        seed_dir=seed_dir if seed_dir.is_dir() else None,
        meta=task_data,
    )


def discover_agents(agents_root: Path | str) -> dict[str, AgentSpec]:
    """Load all agents directly under a root directory, keyed by agent id."""
    root = Path(agents_root).expanduser().resolve()
    if not root.is_dir():
        raise SpecError(f"agents root not found: {root}")
    agents: dict[str, AgentSpec] = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "meta.yaml").is_file():
            agent = load_agent(child)
            if agent.id in agents:
                raise SpecError(f"duplicate agent id `{agent.id}` ({child})")
            agents[agent.id] = agent
    return agents


def discover_tasks(tasks_root: Path | str) -> dict[str, TaskSpec]:
    """Load all tasks under a root directory (recursively), keyed by task id.

    A task is any directory containing a `task.yaml`. Tasks are grouped under
    subdirectories (custom/, benchmark/, swe-bench-pro/), so the search walks
    the whole tree rather than a single level.
    """
    root = Path(tasks_root).expanduser().resolve()
    if not root.is_dir():
        raise SpecError(f"tasks root not found: {root}")
    tasks: dict[str, TaskSpec] = {}
    for task_yaml in sorted(root.rglob("task.yaml")):
        task = load_task(task_yaml.parent)
        if task.id in tasks:
            raise SpecError(f"duplicate task id `{task.id}` ({task_yaml.parent})")
        tasks[task.id] = task
    return tasks
