"""Fetch and describe JobBench tasks.

The dataset lives on Hugging Face at ``JobBench/job-bench`` and is downloaded
into a local cache at setup time. The cache is keyed by the resolved dataset
revision so a run manifest can name the exact data it scored against.

Upstream's ``setup.sh`` renames the two split directories on the way down --
HF ``dataset/`` becomes local ``main/``, HF ``dataset_easy/`` becomes local
``easy/``. We keep that convention so paths are recognizable to anyone who has
used the reference runners.

Layout of one task, as published:

    <split>/<occupation>/task<N>/
        task_folder/                 the ONLY thing the agent may see
            TASK_INSTRUCTIONS.txt    required; the prompt
            ...                      source files the task operates on
        files_required_to_search/    main split only; withheld from the agent,
                                     which is expected to find equivalents on
                                     the open web
        RUBRICS.json                 grading key; withheld
        task_card.md                 human-readable brief; withheld
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ID = "JobBench/job-bench"
REPO_TYPE = "dataset"

# Local split name -> path prefix within the HF repo.
SPLITS: dict[str, str] = {"main": "dataset", "easy": "dataset_easy"}

INSTRUCTIONS_NAME = "TASK_INSTRUCTIONS.txt"
RUBRICS_NAME = "RUBRICS.json"
TASK_FOLDER_NAME = "task_folder"
SEARCH_FILES_NAME = "files_required_to_search"

_REVISION_STAMP = ".jobbench-revision"


class DatasetError(RuntimeError):
    """Dataset is missing, incomplete, or malformed."""


def cache_root() -> Path:
    """Where downloaded splits live.

    Defaults to ``dataset-cache/`` beside this harness (gitignored). Override
    with ``JOBBENCH_CACHE_DIR`` to share one download across checkouts.
    """
    override = os.environ.get("JOBBENCH_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "dataset-cache").resolve()


def split_root(split: str) -> Path:
    _validate_split(split)
    return cache_root() / split


def _validate_split(split: str) -> None:
    if split not in SPLITS:
        raise DatasetError(f"unknown split {split!r}; expected one of {sorted(SPLITS)}")


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def fetch(split: str, *, force: bool = False) -> Path:
    """Download one split into the cache. Idempotent unless ``force``.

    Downloads to a staging directory and moves the split into place only after
    the transfer succeeds, so an interrupted fetch can never leave a
    half-populated tree that later looks complete to ``discover``.
    """
    _validate_split(split)
    dest = split_root(split)

    if dest.exists() and any(dest.iterdir()) and not force:
        return dest

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - environment problem
        raise DatasetError("huggingface-hub is not installed; run `uv sync`") from exc

    prefix = SPLITS[split]
    staging = cache_root() / f".staging-{split}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    local = snapshot_download(
        repo_id=REPO_ID,
        repo_type=REPO_TYPE,
        allow_patterns=[f"{prefix}/**"],
        local_dir=str(staging),
    )

    produced = Path(local) / prefix
    if not produced.is_dir():
        raise DatasetError(f"download did not produce {prefix}/ under {local}")

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(produced), str(dest))

    revision = _resolve_revision()
    (dest / _REVISION_STAMP).write_text(revision, encoding="utf-8")

    shutil.rmtree(staging, ignore_errors=True)
    return dest


def _resolve_revision() -> str:
    """Best-effort commit sha of the dataset repo, for the run manifest."""
    try:
        from huggingface_hub import HfApi

        return HfApi().repo_info(REPO_ID, repo_type=REPO_TYPE).sha or "unknown"
    except Exception:  # noqa: BLE001 - provenance is best effort, never fatal
        return "unknown"


def revision(split: str) -> str:
    stamp = split_root(split) / _REVISION_STAMP
    if stamp.is_file():
        return stamp.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"


# --------------------------------------------------------------------------
# Task model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One JobBench task, as it exists on the host."""

    split: str
    occupation: str
    task_num: int
    root: Path

    @property
    def id(self) -> str:
        """Stable identifier, e.g. ``biostatisticians__task1``."""
        return f"{self.occupation}__task{self.task_num}"

    @property
    def slug(self) -> str:
        """Filesystem-safe directory name for this task's trial output."""
        return self.id

    @property
    def selector(self) -> str:
        """How a user names this task on the CLI, e.g. ``biostatisticians/task1``."""
        return f"{self.occupation}/task{self.task_num}"

    @property
    def task_folder(self) -> Path:
        return self.root / TASK_FOLDER_NAME

    @property
    def instructions_path(self) -> Path:
        return self.task_folder / INSTRUCTIONS_NAME

    @property
    def rubrics_path(self) -> Path:
        return self.root / RUBRICS_NAME

    @property
    def has_search_files(self) -> bool:
        """True when the task expects the agent to find withheld material online."""
        return (self.root / SEARCH_FILES_NAME).is_dir()

    def instructions(self) -> str:
        if not self.instructions_path.is_file():
            raise DatasetError(f"{self.id}: missing {INSTRUCTIONS_NAME}")
        return self.instructions_path.read_text(encoding="utf-8")

    def rubrics(self) -> list[dict]:
        if not self.rubrics_path.is_file():
            raise DatasetError(f"{self.id}: missing {RUBRICS_NAME}")
        data = json.loads(self.rubrics_path.read_text(encoding="utf-8"))
        # Upstream accepts either key; `rubrics` wins when both are present.
        rubrics = data.get("rubrics") or data.get("evaluation_rubrics") or []
        if not rubrics:
            raise DatasetError(f"{self.id}: {RUBRICS_NAME} declares no rubrics")
        return rubrics

    def rubric_count(self) -> int:
        return len(self.rubrics())

    def max_score(self) -> int:
        return sum(int(r.get("weight", 0)) for r in self.rubrics())

    def input_bytes(self) -> int:
        """Total size of what gets seeded into the container."""
        return sum(p.stat().st_size for p in self.task_folder.rglob("*") if p.is_file())


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def discover(split: str) -> list[Task]:
    """All tasks in a split, sorted by occupation then task number.

    A directory only counts as a task when it actually carries the two things
    every downstream stage needs: a prompt for the agent and a rubric for the
    judge. Anything else is a malformed download, not a task we silently skip.
    """
    root = split_root(split)
    if not root.is_dir():
        raise DatasetError(f"split {split!r} is not downloaded; run `run.py fetch --split {split}`")

    tasks: list[Task] = []
    for occupation_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in occupation_dir.iterdir() if p.is_dir()):
            num = _task_number(task_dir.name)
            if num is None:
                continue
            task = Task(
                split=split,
                occupation=occupation_dir.name,
                task_num=num,
                root=task_dir,
            )
            if not task.instructions_path.is_file():
                raise DatasetError(
                    f"{task.selector}: missing {TASK_FOLDER_NAME}/{INSTRUCTIONS_NAME}"
                )
            if not task.rubrics_path.is_file():
                raise DatasetError(f"{task.selector}: missing {RUBRICS_NAME}")
            tasks.append(task)

    if not tasks:
        raise DatasetError(f"split {split!r} contains no tasks under {root}")
    return tasks


def _task_number(name: str) -> int | None:
    """``task12`` -> 12. Anything else -> None.

    Stricter than the reference runner's ``task[0-9]*`` glob, which also matches
    names like ``task1_backup``. The judge's own walker uses ``task[0-9]+``, so
    matching the judge keeps discovery and grading agreed on what a task is.
    """
    if not name.startswith("task"):
        return None
    suffix = name[len("task") :]
    return int(suffix) if suffix.isdigit() else None


def resolve(split: str, selectors: list[str] | None) -> list[Task]:
    """Pick tasks by ``occupation/taskN`` selector, or all when none given."""
    tasks = discover(split)
    if not selectors:
        return tasks

    by_selector = {t.selector: t for t in tasks}
    by_id = {t.id: t for t in tasks}
    chosen: list[Task] = []
    for sel in selectors:
        task = by_selector.get(sel) or by_id.get(sel)
        if task is None:
            raise DatasetError(
                f"no task {sel!r} in split {split!r}; expected e.g. {tasks[0].selector!r}"
            )
        chosen.append(task)
    return chosen
