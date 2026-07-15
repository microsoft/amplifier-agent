"""AutomationBench task loader: fetch task_info on the fly -> runnable trial.

Given a `tasks/automation-bench/<id>/meta.yaml` (benchmark + task + timeout),
this loader materializes a runnable trial AT RUNTIME without importing
`automationbench` on the host and without vendoring any AutomationBench content:

  1. read meta.yaml (selector only: benchmark, task, example_id, timeout)
  2. fetch the task_info dict (prompt, zapier_tools, initial_state, assertions)
     on the fly from the pinned upstream AutomationBench repo -- cloned AT MOST
     ONCE per machine and reused for every task/run (see
     `automation_bench_support.dataset`). The dict is materialized into the
     gitignored per-run `runtime_dir` as `task_info.json`.
  3. build the agent-facing scenario (a preamble teaching the `ab-tool` CLI plus
     the task's own user request)
  4. synthesize a DTU profile that installs AutomationBench into a uv-managed
     python-3.13 venv and drops an `ab-tool` shim on PATH
  5. materialize the seeded workspace (ab_tool.py + task_info.json) and the
     grader_data_dir (the same two files) the deterministic grader reads

Unlike the old vendored layout, `task_info.json` is NOT stored in source control;
it is fetched at load time. The host harness still never imports `automationbench`
directly -- extraction runs in an isolated uv env (see the support module). Only
the DTU imports it at runtime (via the profile's provision commands). Grading is
deterministic (assertions on the final world state), selected by the shared
`tasks/automation-bench/grader.yaml` `type:`.

Modeled on `eval.task_loaders.swe_bench` (the other loader-driven benchmark that
fetches its data on the fly).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import yaml

from eval.schema import TaskDeliverable, TaskSpec
from eval.task_loaders.automation_bench_support import dataset as ab_dataset
from eval.task_loaders.base import LoadedTask

logger = logging.getLogger(__name__)

# In-DTU conventions (fixed contract shared with the lifecycle + grader).
WORKSPACE_DIR = "/workspace"
WORLD_FILE = "/workspace/.ab_world.json"
AB_VENV = "/opt/ab-venv"
AB_TOOL_DIR = "/opt/ab-tool"
AB_TOOL_SHIM = "/usr/local/bin/ab-tool"
AB_TOOL_PY = f"{AB_TOOL_DIR}/ab_tool.py"

# The AutomationBench pip package (installed into the DTU venv, never the host).
AB_PACKAGE = "git+https://github.com/zapier/AutomationBench@main"

# Files vendored per task / shipped by this repo. `ab_tool.py` is the tool-bridge
# CLI; `task_info.json` is the single per-task data source.
AB_TOOL_SRC = Path(__file__).resolve().parents[1] / "automation_bench" / "ab_tool.py"
TASK_INFO_FILENAME = "task_info.json"


def _build_scenario(task_info: dict) -> str:
    """The task handed to the agent: teach the `ab-tool` CLI, then the request.

    A concise preamble explains that the simulated business environment is
    ALREADY seeded and is driven ONLY through the `ab-tool` shell CLI (discover
    endpoints with `search`, act with `fetch`). We append the task's own system
    guidance and user request verbatim. We deliberately do NOT reveal which
    endpoints to call -- discovery is part of what the benchmark measures.
    """
    prompt = task_info.get("prompt", [])
    system_msg = ""
    user_msg = ""
    for msg in prompt:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = str(msg.get("content", ""))
        if role == "system":
            system_msg = content
        elif role == "user":
            user_msg = content

    preamble = (
        "You are operating against a simulated business environment (email, CRM, "
        "and other SaaS services) that is ALREADY seeded with data. You interact "
        "with it ONLY through a shell CLI called `ab-tool`, which mirrors a generic "
        "REST API surface:\n\n"
        "  - Discover available endpoints:\n"
        '      ab-tool search --query "<keywords>"\n'
        "  - Read or modify data with a REST-style call:\n"
        '      ab-tool fetch --method <GET|POST|PATCH|...> --url "<endpoint-url>" '
        "[--params '<json>'] [--body '<json>']\n"
        "  - Base64-encode text if a call requires it:\n"
        '      ab-tool encode --text "<text>"\n\n'
        "Endpoint URLs are not given to you; find them with `ab-tool search` and "
        "read each result's schema before calling `ab-tool fetch`. Complete the "
        "user's request by making the appropriate calls so the environment ends in "
        "the correct final state. Do not ask clarifying questions; make reasonable "
        "assumptions.\n"
    )

    parts = [preamble]
    if system_msg.strip():
        parts.append("=== TASK GUIDANCE ===\n" + system_msg.strip())
    parts.append("=== REQUEST ===\n" + user_msg.strip())
    return "\n\n".join(parts)


def _build_profile(timeout_s: int) -> dict:
    """Synthesize the DTU profile that installs AutomationBench + the ab-tool shim.

    Ubuntu 24.04 with outbound internet. Provisioning installs uv, creates a
    python-3.13 venv at /opt/ab-venv, pip-installs AutomationBench into it, then
    drops the `ab-tool` shim on PATH (execs the venv python against ab_tool.py
    with AB_WORLD_FILE exported). No agent-under-test is installed here.
    """
    shim = (
        "#!/usr/bin/env bash\n"
        f"export AB_WORLD_FILE={WORLD_FILE}\n"
        f'exec {AB_VENV}/bin/python {AB_TOOL_PY} "$@"\n'
    )
    # Heredoc that writes the shim inside the DTU (quoted marker: no expansion).
    write_shim = f"cat > {AB_TOOL_SHIM} <<'AB_SHIM_EOF'\n{shim}AB_SHIM_EOF\nchmod +x {AB_TOOL_SHIM}"
    return {
        "name": "eval-task-automation-bench",
        "description": (
            "AutomationBench task environment: Ubuntu 24.04 with a uv-managed "
            "python-3.13 venv that has AutomationBench installed and an `ab-tool` "
            "CLI on PATH. No agent-under-test is installed here."
        ),
        "base": {"image": "ubuntu:24.04"},
        "passthrough": {"allow_external": True},
        "provision": {
            "setup_cmds": [
                "apt-get update && apt-get install -y --no-install-recommends "
                "git curl ca-certificates && rm -rf /var/lib/apt/lists/*",
                "curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh",
                "uv python install 3.13",
                f"uv venv --python 3.13 {AB_VENV}",
                # `uv venv` intentionally omits pip; `uv pip install --python` manages the
                # venv externally, so no in-venv pip bootstrap is needed.
                f"uv pip install --python {AB_VENV}/bin/python {AB_PACKAGE}",
                f"mkdir -p {WORKSPACE_DIR} {AB_TOOL_DIR}",
                write_shim,
            ]
        },
        "readiness": [
            {
                "name": "automationbench-importable",
                "command": f'{AB_VENV}/bin/python -c "import automationbench"',
            }
        ],
    }


class AutomationBenchTaskLoader:
    """Load an automation-bench task dir into a runnable `LoadedTask` at runtime."""

    name = "automation-bench"

    def handles(self, task_dir: Path) -> bool:
        """True when the task dir is an automation-bench dir (meta.yaml, no task.yaml)."""
        from eval.task_loaders.base import is_automation_bench_dir

        return is_automation_bench_dir(Path(task_dir))

    def read_meta(self, task_dir: Path) -> dict:
        """Read + shallowly validate an automation-bench meta.yaml (no import)."""
        meta_path = Path(task_dir) / "meta.yaml"
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{meta_path}: expected a YAML mapping")
        if str(data.get("benchmark", "")).strip() != "automation-bench":
            raise ValueError(
                f"{meta_path}: `benchmark:` must be 'automation-bench' "
                f"(got {data.get('benchmark')!r})"
            )
        if not str(data.get("task", "")).strip():
            raise ValueError(f"{meta_path}: `task:` must be a non-empty string")
        return data

    async def load(
        self,
        task_dir: Path,
        *,
        runtime_dir: Path,
        mode: str = "agent",
    ) -> LoadedTask:
        """Materialize a runnable `LoadedTask` by fetching task_info on the fly.

        Args:
            task_dir: The task dir holding meta.yaml (selector only; no vendored
                task_info.json).
            runtime_dir: Host dir for the generated profile, seeded workspace,
                grader data dir, and the fetched/materialized task_info.json.
            mode: Unused (automation-bench only measures an agent); kept for the
                TaskLoader protocol.
        """
        task_dir = Path(task_dir).expanduser().resolve()
        runtime_dir = Path(runtime_dir).expanduser().resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)

        meta = self.read_meta(task_dir)
        task_name = str(meta["task"]).strip()
        timeout_s = int(meta.get("timeout", 1800))

        # Fetch task_info on the fly from the pinned upstream AutomationBench repo
        # (cloned AT MOST ONCE per machine, reused for every task/run) and
        # materialize it into the gitignored per-run runtime_dir. The host still
        # never imports automationbench: extraction runs in an isolated uv env
        # inside the support module. `fetch_task` already normalizes `info` to a
        # nested dict.
        task_info_path = runtime_dir / TASK_INFO_FILENAME
        task_info = ab_dataset.fetch_task(task_name, out_path=task_info_path)

        if not AB_TOOL_SRC.is_file():
            raise FileNotFoundError(f"ab_tool.py not found at {AB_TOOL_SRC}")

        # 1) Generated DTU profile (installs AutomationBench + ab-tool shim).
        profile = _build_profile(timeout_s)
        profile_path = runtime_dir / "profile.yaml"
        profile_path.write_text(
            yaml.safe_dump(profile, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        logger.info("automation-bench loader: wrote generated profile -> %s", profile_path)

        # 2) Grader data dir: the two files the deterministic grader pushes in.
        grader_data_dir = runtime_dir / "grader_data"
        grader_data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(AB_TOOL_SRC, grader_data_dir / "ab_tool.py")
        shutil.copyfile(task_info_path, grader_data_dir / TASK_INFO_FILENAME)

        # 3) Seeded workspace: ab_tool.py + task_info.json land in /workspace so
        # the lifecycle can seed the world (`ab-tool seed`) before the agent runs.
        workspace_dir = runtime_dir / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(AB_TOOL_SRC, workspace_dir / "ab_tool.py")
        shutil.copyfile(task_info_path, workspace_dir / TASK_INFO_FILENAME)

        # 4) The runnable spec. The deliverable is the final world state at
        # /workspace; the grader is deterministic (assertions on that world).
        scenario = _build_scenario(task_info)
        grader_path = task_dir.parent / "grader.yaml"
        if not grader_path.is_file():
            raise FileNotFoundError(
                f"no automation-bench grader.yaml found for {task_dir} (looked at {grader_path})"
            )

        task = TaskSpec(
            id=task_name,
            dir=task_dir,
            description=f"AutomationBench task {task_name}",
            scenario=scenario,
            timeout_s=timeout_s,
            deliverable=TaskDeliverable(
                path=WORKSPACE_DIR,
                description="the final simulated world state; graded by deterministic assertions",
            ),
            grader_path=grader_path,
            profile_path=profile_path,
            workspace_dir=workspace_dir,
            seed_dir=None,
            meta={"meta": meta, "task": task_name, "loader": self.name},
        )

        return LoadedTask(
            task=task,
            profile_path=profile_path,
            grader_data_dir=grader_data_dir,
            loader=self.name,
            extras={"task": task_name},
        )


__all__ = [
    "AutomationBenchTaskLoader",
    "WORKSPACE_DIR",
    "WORLD_FILE",
    "AB_VENV",
    "AB_TOOL_DIR",
    "AB_TOOL_PY",
    "AB_TOOL_SHIM",
    "TASK_INFO_FILENAME",
]
