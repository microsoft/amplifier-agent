"""SWE-bench Pro task loader: HuggingFace fetch + Dockerfile -> Incus profile.

Given a `tasks/swe-bench-pro/instances/<id>/meta.yaml` (only instance_id +
dataset + timeout are stored), this loader materializes a runnable trial AT
RUNTIME (nothing from the benchmark is vendored):

  1. fetch the instance row from HuggingFace (ScaleAI/SWE-bench_Pro)
  2. shallow-clone the official scaleapi repo for the base+instance Dockerfiles,
     run_script.sh, and parser.py
  3. convert the Dockerfiles into a DTU/Incus profile (Docker Hub images cannot
     be pulled by the Incus engine, so the environment is reconstructed)
  4. synthesize the deterministic grader's runtime config from the fetched test
     lists (fail_to_pass / pass_to_pass) and the official grading assets

It returns a `LoadedTask` whose:
  - `profile_path` is the generated Incus profile (the reconstructed env),
  - `task.scenario` is the GitHub issue (the prompt handed to the agent),
  - `task.timeout_s` is the instance's budget,
  - `grader_data_dir` holds `deterministic_config.json` + the copied
    run_script.sh / parser.py the deterministic grader pushes into the DTU,
  - `extras` carries the gold patch and repo id for the lifecycle.

Copy-adapted from the proven prior-art `harness.py` + `swe_bench_pro` package,
rehosted on this harness's DTU/install building blocks and typed specs.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import yaml

from eval.schema import TaskDeliverable, TaskSpec
from eval.task_loaders.base import LoadedTask
from eval.task_loaders.swe_bench_support import dataset, dockerfile_convert, official_assets

logger = logging.getLogger(__name__)

# The repo is checked out at /app inside the reconstructed environment (the
# official base Dockerfiles clone it there). Grading scratch lives in /grading,
# kept separate so pushed assets never pollute the repo's git diff.
REPO_DIR = "/app"
GRADING_DIR = "/grading"

# Filenames the loader writes into the grader_data_dir; the deterministic grader
# reads this exact contract.
CONFIG_FILENAME = "deterministic_config.json"
RUN_SCRIPT_FILENAME = "run_script.sh"
PARSER_FILENAME = "parser.py"


def build_scenario(row: dict) -> str:
    """The task handed to the agent: the issue only (matches published methodology)."""
    return (
        "You are working in a software repository checked out at /workspace. "
        "Resolve the issue described below by editing the code in that repository. "
        "Make the minimal changes needed to fix the problem. Do not edit or add "
        "tests; the grader supplies its own. Leave your changes in the working "
        "tree when you are done.\n\n"
        "=== ISSUE ===\n"
        f"{row['problem_statement']}"
    )


def _resolve_grader_path(task_dir: Path) -> Path:
    """Find the deterministic grader.yaml for a swe-bench instance.

    Prefer a per-instance `grader.yaml`; otherwise fall back to the shared
    `grader.yaml` at the swe-bench group root (`instances/<id>/ -> <group>/`).
    """
    per_instance = task_dir / "grader.yaml"
    if per_instance.is_file():
        return per_instance
    shared = task_dir.parents[1] / "grader.yaml"
    if shared.is_file():
        return shared
    raise FileNotFoundError(
        f"no deterministic grader.yaml found for {task_dir} (looked at {per_instance} and {shared})"
    )


class SweBenchTaskLoader:
    """Load a swe-bench instance dir into a runnable `LoadedTask` at runtime."""

    name = "swe_bench"

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        """Construct the loader.

        Args:
            cache_dir: Optional local clone of the official scaleapi repo. When
                None the repo is shallow-cloned into a temp dir on first use.
        """
        self.cache_dir = cache_dir

    def handles(self, task_dir: Path) -> bool:
        """A swe-bench task dir has a meta.yaml with instance_id + dataset, no task.yaml."""
        from eval.task_loaders.base import is_swe_bench_dir

        return is_swe_bench_dir(Path(task_dir))

    def read_meta(self, task_dir: Path) -> dict:
        """Read + shallowly validate an instance meta.yaml (no network)."""
        meta_path = Path(task_dir) / "meta.yaml"
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{meta_path}: expected a YAML mapping")
        if not str(data.get("instance_id", "")).strip():
            raise ValueError(f"{meta_path}: `instance_id:` must be a non-empty string")
        if not str(data.get("dataset", "")).strip():
            raise ValueError(f"{meta_path}: `dataset:` must be a non-empty string")
        return data

    async def load(
        self,
        task_dir: Path,
        *,
        runtime_dir: Path,
        mode: str = "gold",
    ) -> LoadedTask:
        """Fetch + convert the instance and materialize a runnable `LoadedTask`.

        Args:
            task_dir: The instance dir holding meta.yaml.
            runtime_dir: Host dir for the generated profile + grader data.
            mode: "gold" (grade the dataset reference patch) or "agent" (grade an
                agent's captured patch). The loader is mode-agnostic except that
                the grader's candidate patch file is written by the lifecycle
                later; the loader only records the gold patch in `extras`.
        """
        task_dir = Path(task_dir).expanduser().resolve()
        runtime_dir = Path(runtime_dir).expanduser().resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)

        meta = self.read_meta(task_dir)
        instance_id = str(meta["instance_id"]).strip()
        timeout_s = int(meta.get("timeout", 7200))

        logger.info("swe_bench loader: fetching instance row + official assets for %s", instance_id)
        row = dataset.fetch_instance(instance_id)
        assets = official_assets.fetch_assets(instance_id, self.cache_dir)

        # 1) Reconstructed environment profile (Docker -> Incus).
        profile = dockerfile_convert.convert(
            assets.base_dockerfile,
            assets.instance_dockerfile,
            instance_id=instance_id,
        )
        profile_path = runtime_dir / "profile.yaml"
        profile_path.write_text(dockerfile_convert.to_yaml(profile), encoding="utf-8")
        logger.info("swe_bench loader: wrote generated profile -> %s", profile_path)

        # 2) Deterministic grader runtime config + official grading assets.
        grader_data_dir = runtime_dir / "grader_data"
        grader_data_dir.mkdir(parents=True, exist_ok=True)
        exports = dockerfile_convert.env_exports(assets.base_dockerfile, assets.instance_dockerfile)
        config = {
            "instance_id": instance_id,
            "repo": row.get("repo"),
            "base_commit": row["base_commit"],
            "before_repo_set_cmd": row["before_repo_set_cmd"],
            "selected_test_files": dataset.as_list(row["selected_test_files_to_run"]),
            "exports": exports,
            "fail_to_pass": dataset.as_list(row["fail_to_pass"]),
            "pass_to_pass": dataset.as_list(row["pass_to_pass"]),
            "repo_dir": REPO_DIR,
            "grading_dir": GRADING_DIR,
        }
        (grader_data_dir / CONFIG_FILENAME).write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
        shutil.copyfile(assets.run_script_path, grader_data_dir / RUN_SCRIPT_FILENAME)
        shutil.copyfile(assets.parser_path, grader_data_dir / PARSER_FILENAME)

        # 3) The runnable spec. swe-bench has no seeded workspace/ or file
        # deliverable in the static sense; the deliverable is the repo working
        # tree at /app, and the grader is deterministic (test pass/fail).
        scenario = build_scenario(row)
        grader_path = _resolve_grader_path(task_dir)
        task = TaskSpec(
            id=instance_id,
            dir=task_dir,
            description=f"SWE-bench Pro instance {instance_id} ({row.get('repo')})",
            scenario=scenario,
            timeout_s=timeout_s,
            deliverable=TaskDeliverable(
                path=REPO_DIR,
                description="the repository working tree; graded by test pass/fail",
            ),
            grader_path=grader_path,
            profile_path=profile_path,
            workspace_dir=task_dir,  # placeholder: swe-bench seeds nothing
            seed_dir=None,
            meta={"meta": meta, "repo": row.get("repo"), "loader": self.name},
        )

        return LoadedTask(
            task=task,
            profile_path=profile_path,
            grader_data_dir=grader_data_dir,
            loader=self.name,
            extras={
                "instance_id": instance_id,
                "repo": row.get("repo"),
                "gold_patch": row["patch"],
                "problem_statement": row.get("problem_statement", ""),
                "run_script_path": str(assets.run_script_path),
                "parser_path": str(assets.parser_path),
            },
        )


__all__ = [
    "SweBenchTaskLoader",
    "build_scenario",
    "REPO_DIR",
    "GRADING_DIR",
    "CONFIG_FILENAME",
    "RUN_SCRIPT_FILENAME",
    "PARSER_FILENAME",
]
