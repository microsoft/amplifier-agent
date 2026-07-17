"""High-level orchestration of the warm DTU (used by cli.py and conftest.py).

Ties together the Gitea mirror, the profile launch, and the state file into the
four lifecycle verbs the harness exposes: provision / is_warm / refresh / teardown.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from . import dtu, state
from .progress import log

# tests/e2e/framework/dtu_manager.py -> framework -> e2e -> tests -> amplifier-agent (repo root)
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
PROFILE_REL = ".amplifier/digital-twin-universe/profiles/e2e.yaml"
DTU_ASSETS_REL = "tests/e2e/framework/provisioning"

GITEA_NAME = "aa-e2e"
DTU_NAME = "aa-e2e"

# Default in-DTU server coordinates. The `server` fixture starts the HTTP server;
# these are recorded in the state file so tests know where to reach it.
DEFAULT_SERVER_BASE_URL = "http://127.0.0.1:9099"
DEFAULT_SERVER_TOKEN = "local-dev-secret"


def _mirror_repos(gitea: dict[str, Any]) -> list[str]:
    """Ensure + snapshot-push every dirty (and always amplifier-agent) repo. Returns them."""
    repos = dtu.dirty_repos(str(WORKSPACE_ROOT))
    for repo in repos:
        local_path = WORKSPACE_ROOT / repo
        dtu.ensure_repo(gitea["port"], gitea["token"], repo)
        dtu.snapshot_push(str(local_path), gitea["port"], gitea["token"], repo)
    return repos


def _build_varmap(gitea: dict[str, Any]) -> dict[str, str]:
    """Assemble the --var map for launch/update."""
    return {
        "GITEA_URL": gitea["gitea_url"],
        "GITEA_TOKEN": gitea["token"],
        "AA_E2E_BASE_IMAGE": "ubuntu:24.04",
    }


def _stage_launch_dir() -> str:
    """Copy the profile + dtu assets into a temp dir so profile ./dtu/... paths resolve.

    Returns the path to the staged profile YAML.
    """
    tmp = tempfile.mkdtemp(prefix="aa-e2e-launch-")
    profile_src = REPO_ROOT / PROFILE_REL
    profile_dst = Path(tmp) / "e2e.yaml"
    shutil.copyfile(profile_src, profile_dst)

    assets_src = REPO_ROOT / DTU_ASSETS_REL
    assets_dst = Path(tmp) / "dtu"
    shutil.copytree(assets_src, assets_dst)

    return str(profile_dst)


def _warn_extra_repos(mirrored: list[str]) -> None:
    """Warn when a mirrored repo is not redirected inside the DTU.

    The profile only rewrites the amplifier-agent GitHub URL to its Gitea mirror. A
    dirty amplifier-core or amplifier-foundation is snapshotted to Gitea but still
    resolved from GitHub inside the DTU until a matching ``url_rewrites`` rule exists.
    """
    if mirrored != ["amplifier-agent"]:
        print(
            f"[dtu_manager] warning: mirrored {mirrored} but only amplifier-agent is "
            "redirected inside the DTU; add url_rewrites rules to redirect the others."
        )


def _find_instance(name: str) -> dict[str, Any] | None:
    """Return the DTU instance dict named ``name``, or None if it does not exist."""
    for inst in dtu.list_instances():
        if inst.get("id") == name:
            return inst
    return None


def _write_state(dtu_id: str, dtu_name: str, gitea: dict[str, Any]) -> dict[str, Any]:
    new_state: dict[str, Any] = {
        "dtu_id": dtu_id,
        "dtu_name": dtu_name,
        "gitea_id": gitea["id"],
        "gitea_port": gitea["port"],
        "server_base_url": DEFAULT_SERVER_BASE_URL,
        "server_token": DEFAULT_SERVER_TOKEN,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state.write_state(new_state)
    return new_state


def provision() -> dict[str, Any]:
    """Provision a fresh warm DTU: mirror latest code to Gitea, destroy any existing
    aa-e2e container, then launch a clean one.

    Why always fresh (not an in-place ``update``): ``uv tool install --reinstall`` wipes
    amplifier-agent's lazily-installed provider module, which breaks the HTTP ``serve``
    model enumeration (``serve`` exits 2). A clean launch reliably yields a working CLI
    *and* server, so every ``run`` rebuilds rather than updating in place. A fresh launch
    is ~90s. Use ``--skip-setup`` to re-run against the existing container, or ``refresh``
    for a fast code-only in-place update (CLI-only iteration; leaves ``serve`` broken).
    """
    log("provision: starting fresh DTU provision")
    gitea = dtu.ensure_gitea(name=GITEA_NAME)
    _warn_extra_repos(_mirror_repos(gitea))
    varmap = _build_varmap(gitea)

    existing = _find_instance(DTU_NAME)
    if existing:
        log(f"provision: existing '{DTU_NAME}' found; destroying for a clean rebuild")
        dtu.destroy(existing["id"])

    profile_path = _stage_launch_dir()
    launched = dtu.launch(profile_path, varmap, name=DTU_NAME)
    dtu_id = launched["id"]
    dtu.wait_ready(dtu_id)
    result = _write_state(dtu_id, launched.get("name", DTU_NAME), gitea)
    log("provision: done; DTU is warm and state written")
    return result


def is_warm() -> bool:
    """True if a state file exists and its DTU is currently ready."""
    current = state.read_state()
    if not current:
        return False
    try:
        return dtu.check_ready(current["dtu_id"])
    except Exception:
        return False


def refresh() -> None:
    """Re-mirror local repos and re-run the in-DTU install in place (no relaunch)."""
    current = state.read_state()
    if not current:
        raise RuntimeError("no warm DTU to refresh; run `up` first")

    log("refresh: re-mirroring code and updating DTU in place")
    gitea = dtu.ensure_gitea(name=GITEA_NAME)
    _mirror_repos(gitea)
    varmap = _build_varmap(gitea)
    dtu.update(current["dtu_id"], varmap)
    log("refresh: done")


def teardown() -> None:
    """Destroy the DTU instance (if any) and clear state. Leaves Gitea running."""
    current = state.read_state()
    if current:
        dtu.destroy(current["dtu_id"])
    state.clear_state()
    log("teardown: state cleared")
