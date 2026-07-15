"""Dataclasses for the consolidated evaluation harness.

These types are the harness's stable surface area. Loaders produce `AgentSpec`
and `TaskSpec` from the on-disk layout; the trial loop consumes
`TrialSpec` and emits `TrialResult`. `TrialState` is the on-disk state-machine
vocabulary.

Modeled on the upstream `amplifier_evaluation.harness.schema`, but adapted to
this harness's on-disk layout: the agent carries a statically configured model
(no templating) and structured install/extract data, and the
task consolidates identity, scenario, timeout, deliverable pointer, and grader
and profile references into a single `task.yaml`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class TrialState(StrEnum):
    """States in a trial's lifecycle.

    Persisted to disk as the `state` field in a trial's `state.json`. Terminal
    states (`completed`, `failed`, `cancelled`) are not advanced further by the
    scheduler unless an external operator requests a retry.
    """

    PENDING = "pending"
    LAUNCHING = "launching"
    INSTALLING = "installing"
    SEEDING = "seeding"
    RUNNING_AGENT = "running_agent"
    EXTRACTING = "extracting"
    GRADING = "grading"
    CLEANING_UP = "cleaning_up"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TrialState.COMPLETED, TrialState.FAILED, TrialState.CANCELLED)


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AgentInstall:
    """Agent install directives loaded from `install.yaml`.

    `setup_cmds` run inside the DTU. `required_env` names host
    environment variables that must be present and are forwarded into the DTU.
    """

    setup_cmds: list[str]
    required_env: list[str] = field(default_factory=list)


@dataclass
class AgentSpec:
    """One agent loaded from a directory under `agents/`.

    The model is set statically here (via `meta.yaml`), never templated per run.
    """

    id: str
    dir: Path
    description: str
    model: str
    install: AgentInstall
    invocation_md_path: Path
    invocation_md: str
    extract_path: Path
    extract: dict[str, Any]
    meta: dict[str, Any]


@dataclass
class TaskDeliverable:
    """Where a task's deliverable is expected inside the DTU."""

    path: str
    description: str = ""


@dataclass
class TaskSpec:
    """One task loaded from a directory under `tasks/`.

    `scenario` is the natural-language brief handed to the AI User. `grader_path`
    and `profile_path` point at sibling files resolved by the loader. `seed_dir`
    is present only when the task declares prior sessions to plant pre-agent.
    """

    id: str
    dir: Path
    description: str
    scenario: str
    timeout_s: int
    deliverable: TaskDeliverable
    grader_path: Path
    profile_path: Path
    workspace_dir: Path
    seed_dir: Path | None
    meta: dict[str, Any]


@dataclass
class TrialSpec:
    """One trial: an (agent, task, trial_number) tuple ready to run."""

    agent: AgentSpec
    task: TaskSpec
    trial_number: int
    launch_variables: dict[str, str] | None = None

    @property
    def trial_id(self) -> str:
        return f"{self.agent.id}__{self.task.id}__trial-{self.trial_number}"


@dataclass
class StageRecord:
    """One stage transition entry in a trial's history."""

    state: str
    at: str  # ISO8601 UTC
    note: str | None = None


@dataclass
class TrialResult:
    """Outcome of one trial. Mirrors the final state.json."""

    trial_id: str
    agent_id: str
    task_id: str
    trial_number: int
    state: str
    dtu_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_s: float = 0.0
    error: str | None = None
    # Per-stage outcomes. Each is a small JSON-safe summary; full artifacts live
    # next to state.json on disk.
    ai_user: dict[str, Any] | None = None
    extractor: dict[str, Any] | None = None
    grader: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    history: list[StageRecord] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)
