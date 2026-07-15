"""Provision an agent and a task into a running Digital Twin Universe instance.

Three responsibilities, matching the trial lifecycle:

- `compose_launch_profile(agent, task, output_path)`: read the task's DTU
  profile, ensure every var the agent declares in `install.required_env` is
  covered by a `passthrough.services` entry, and write the merged profile to
  `output_path`. This is what actually gets launched, so the agent's required
  env (e.g. ANTHROPIC_API_KEY) reaches the container without the task profile
  having to enumerate every possible key.
- `install_agent(agent, dtu)`: run the agent's `setup_cmds` inside the DTU,
  each via `bash -lc` so heredocs and `$VAR` expansion work.
- `seed_workspace(task, dtu)`: push the task's `workspace/` contents into
  `/workspace` and write an instructions file the agent driver can point at.

Reimplemented for this harness from the reference library
(`amplifier_evaluation.harness.install`), adapted to this harness's typed
`AgentSpec`/`TaskSpec` (structured `install`/`extract`) rather than raw dicts.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from eval.dtu import DTU
from eval.schema import AgentSpec, TaskSpec

logger = logging.getLogger(__name__)


# Default in-container locations. `/workspace` is the task workspace root; the
# instructions file is written alongside the seeded files so the agent driver
# has a single, predictable pointer.
WORKSPACE_ROOT = "/workspace"
INSTRUCTIONS_PATH = f"{WORKSPACE_ROOT}/INSTRUCTIONS.md"


class InstallError(RuntimeError):
    """Raised when provisioning the agent or task into the DTU fails."""


def verify_env(agent: AgentSpec) -> list[str]:
    """Return the agent's `required_env` vars that are missing from `os.environ`."""
    return [v for v in agent.install.required_env if not os.environ.get(v)]


def _service_name_for(env_var: str) -> str:
    """Derive a `passthrough.services` `name` label from an env var name.

    Conventions matching the DTU profiles already in tree:
      OPENAI_API_KEY    -> openai
      ANTHROPIC_API_KEY -> anthropic
      MISTRAL_API_KEY   -> mistral
      GITHUB_TOKEN      -> github_token

    The `name` field is just a label inside the DTU profile; forwarding is keyed
    on `key_env`. Keeping the convention consistent with existing profiles avoids
    surprising anyone reading the merged profile dropped into the trial dir.
    """
    lowered = env_var.lower()
    if lowered.endswith("_api_key"):
        return lowered[: -len("_api_key")]
    return lowered


def compose_launch_profile(
    agent: AgentSpec,
    task: TaskSpec,
    output_path: Path,
) -> Path:
    """Merge the agent's required env into the task profile; write it out.

    Reads `task.profile_path`, ensures every var in `agent.install.required_env`
    is covered by a `passthrough.services` entry (adding missing ones), and
    writes the merged profile to `output_path`. Returns `output_path`.

    Task profile entries always win on conflict (matched by `key_env`): a task
    author who deliberately configures a service entry shouldn't have it silently
    rewritten. We only *add* missing entries. The merged profile is always
    written through, so the trial directory holds the exact profile launched.
    """
    raw = task.profile_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise InstallError(
            f"task profile {task.profile_path} did not parse to a mapping; "
            f"got {type(data).__name__}"
        )

    needed = agent.install.required_env
    if needed:
        passthrough = data.setdefault("passthrough", {})
        if not isinstance(passthrough, dict):
            raise InstallError(
                f"task profile {task.profile_path} has a non-mapping "
                f"`passthrough` block ({type(passthrough).__name__})"
            )
        services = passthrough.setdefault("services", [])
        if not isinstance(services, list):
            raise InstallError(
                f"task profile {task.profile_path} has a non-list "
                f"`passthrough.services` ({type(services).__name__})"
            )
        existing = {
            s.get("key_env")
            for s in services
            if isinstance(s, dict) and isinstance(s.get("key_env"), str)
        }
        added: list[str] = []
        for var in needed:
            if var in existing:
                continue
            services.append({"name": _service_name_for(var), "key_env": var})
            added.append(var)
        if added:
            logger.info(
                "compose_launch_profile: injected passthrough for %s into %s",
                added,
                output_path.name,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return output_path


async def install_agent(
    agent: AgentSpec,
    dtu: DTU,
    *,
    log_to: Path | None = None,
    step_timeout_s: float = 1800.0,
) -> None:
    """Install `agent` into `dtu` by running its `setup_cmds`.

    Each command runs via `bash -lc` so the login shell sources the DTU env and
    heredocs/`$VAR` expansion behave. Raises `InstallError` on the first failure.
    """
    cmds = agent.install.setup_cmds
    if not cmds:
        raise InstallError(
            f"agent {agent.id} has no setup_cmds (list of shell commands to run "
            f"inside the task DTU)"
        )

    for i, cmd in enumerate(cmds, start=1):
        logger.info("agent install [%d/%d]: %.80s", i, len(cmds), cmd.splitlines()[0])
        result = await dtu.exec_cmd(
            ["bash", "-lc", cmd],
            timeout_s=step_timeout_s,
            stream_to_logfile=log_to,
        )
        if result.returncode != 0:
            raise InstallError(
                f"agent {agent.id} setup_cmds[{i}] failed (exit {result.returncode}):\n"
                f"  cmd: {cmd}\n"
                f"  stderr: {result.stderr.strip()[:2000]}"
            )


async def seed_workspace(
    task: TaskSpec,
    dtu: DTU,
    *,
    workspace_root: str = WORKSPACE_ROOT,
    instructions_path: str = INSTRUCTIONS_PATH,
) -> None:
    """Push the task's `workspace/` contents into `/workspace` and write instructions.

    Each top-level entry in `task.workspace_dir` is pushed so its contents land
    directly under `workspace_root` (files as `<root>/<name>`, directories
    recursively with their basename preserved). The task scenario is written to
    `instructions_path` so the agent driver has a stable pointer to the brief.
    """
    # Ensure the workspace root exists (the task profile normally makes it, but
    # don't depend on that here so seeding is self-contained).
    mk = await dtu.exec_cmd(["mkdir", "-p", workspace_root], timeout_s=60.0)
    if mk.returncode != 0:
        raise InstallError(
            f"could not create {workspace_root} in DTU {dtu.id}: {mk.stderr.strip()}"
        )

    children = sorted(task.workspace_dir.iterdir())
    for child in children:
        if child.is_dir():
            # `-r` treats the destination as the parent; basename is preserved,
            # so contents land at `<root>/<name>/...`.
            await dtu.file_push(child, f"{workspace_root}/", recursive=True)
        else:
            await dtu.file_push(child, f"{workspace_root}/{child.name}")

    logger.info(
        "seed_workspace: pushed %d top-level item(s) from %s into %s",
        len(children),
        task.workspace_dir,
        workspace_root,
    )

    # Write the scenario to an instructions file inside the DTU. Stage the text
    # host-side, then push it, so we don't have to escape it through a shell.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(task.scenario.rstrip() + "\n")
        tmp_path = Path(tmp.name)
    try:
        await dtu.file_push(tmp_path, instructions_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    logger.info("seed_workspace: wrote instructions to %s", instructions_path)


def _tar_seed_dir(seed_dir: Path, out_tgz: Path) -> None:
    """Tar the seed dir's contents (session dirs at the archive root).

    Equivalent to `tar czf out_tgz -C seed_dir .`, so untarring in the agent's
    session store lays the session directories down directly (no leading seed/).
    """
    result = subprocess.run(
        ["tar", "czf", str(out_tgz), "-C", str(seed_dir), "."],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise InstallError(
            f"tar of seed dir failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _unwrap_stdout(stdout: str) -> str:
    """Return the inner command stdout, unwrapping a DTU JSON envelope if present.

    `dtu.exec_cmd(...).stdout` is already unwrapped by dtu.py's envelope parser,
    but be defensive: if a raw envelope slips through, extract its `stdout`.
    Session ids are unique, so substring matching is safe for either shape.
    """
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout
    if isinstance(payload, dict) and isinstance(payload.get("stdout"), str):
        return payload["stdout"]
    return stdout


def seed_target_base_path(agent: AgentSpec) -> str | None:
    """Return the agent's declared seed-target session-store base path, or None.

    The plant target is agent-owned: an agent opts into being seedable by
    declaring `seed_target.base_path` in its extract.yaml (the on-disk session
    store its recall mechanism reads). Agents without it cannot be seeded.
    """
    seed_target = agent.extract.get("seed_target") if isinstance(agent.extract, dict) else None
    if isinstance(seed_target, dict):
        base = seed_target.get("base_path")
        if isinstance(base, str) and base.strip():
            return base.strip()
    return None


async def seed_sessions(
    agent: AgentSpec,
    task: TaskSpec,
    dtu: DTU,
    *,
    stage_dir: Path | None = None,
) -> dict[str, Any]:
    """Plant a task's prior Amplifier sessions into the agent's session store.

    The seeding seam. Convention-driven opt-in: a task
    seeds prior sessions by shipping a `seed/` directory whose immediate
    subdirectories are session dirs; an agent opts into being seedable by
    declaring `seed_target.base_path` in its extract.yaml. When both are present,
    the seed dirs are tarred, pushed into the agent's session store, untarred,
    and every expected session id is verified present -- BEFORE the agent runs,
    so it can recall them.

    Returns a JSON-safe record describing the outcome (always, never raises for
    a plain no-op):
        seeded: bool
        session_ids: list[str]      # the planted session dir names
        base_path: str | None       # where they were planted
        verify_listing: str         # the in-DTU `ls` of the store after planting
        note: str

    Raises InstallError only when seeding was attempted but the plant or the
    post-plant verification failed (a real error, not a no-op).
    """
    if task.seed_dir is None:
        return {
            "seeded": False,
            "session_ids": [],
            "base_path": None,
            "verify_listing": "",
            "note": "task ships no seed/ dir; session seeding is a no-op",
        }

    base_path = seed_target_base_path(agent)
    if base_path is None:
        return {
            "seeded": False,
            "session_ids": [],
            "base_path": None,
            "verify_listing": "",
            "note": (
                f"agent {agent.id} declares no seed_target.base_path in extract.yaml; "
                "sessions were NOT planted (agent has no recallable session store)"
            ),
        }

    expected = sorted(p.name for p in task.seed_dir.iterdir() if p.is_dir())
    if not expected:
        return {
            "seeded": False,
            "session_ids": [],
            "base_path": base_path,
            "verify_listing": "",
            "note": f"seed/ dir {task.seed_dir} contains no session subdirectories",
        }

    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        seed_tgz = Path(tmp.name)
    try:
        _tar_seed_dir(task.seed_dir, seed_tgz)
        logger.info(
            "seed_sessions: planting %d prior session(s) into %s:%s",
            len(expected),
            dtu.id,
            base_path,
        )
        mk = await dtu.exec_cmd(["mkdir", "-p", base_path], timeout_s=60.0)
        if mk.returncode != 0:
            raise InstallError(
                f"could not create seed target {base_path} in DTU {dtu.id}: {mk.stderr.strip()}"
            )
        await dtu.file_push(seed_tgz, f"{base_path}/seed.tgz")
        untar = await dtu.exec_cmd(
            ["bash", "-lc", f"cd {base_path} && tar xzf seed.tgz && rm -f seed.tgz"],
            timeout_s=120.0,
        )
        if untar.returncode != 0:
            raise InstallError(
                f"seed untar failed in {base_path} (exit {untar.returncode}): "
                f"{untar.stderr.strip()}"
            )
    finally:
        seed_tgz.unlink(missing_ok=True)

    # Verify the plant landed: every expected session dir must be present in the
    # store BEFORE the agent runs. This listing is the seed-present-pre-run proof.
    ls = await dtu.exec_cmd(["bash", "-lc", f"ls -la {base_path}"], timeout_s=60.0)
    listing = _unwrap_stdout(ls.stdout)
    missing = [sid for sid in expected if sid not in listing]
    if missing:
        raise InstallError(
            f"seed verification failed: session dirs {missing} not present in "
            f"{base_path} after planting (listing: {listing[:500]})"
        )
    logger.info("seed_sessions: verified %d session dir(s) present in store", len(expected))

    if stage_dir is not None:
        try:
            stage_dir.mkdir(parents=True, exist_ok=True)
            (stage_dir / "seed_verify.txt").write_text(listing, encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write seed_verify.txt: %s", exc)

    return {
        "seeded": True,
        "session_ids": expected,
        "base_path": base_path,
        "verify_listing": listing,
        "note": (
            f"planted {len(expected)} prior session(s) into {base_path} and verified "
            "all present BEFORE the agent run"
        ),
    }
