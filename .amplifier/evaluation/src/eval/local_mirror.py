"""Deliver the developer's LOCAL amplifier-agent working tree to the DTU.

By default a task profile installs the agent-under-test from a pinned GitHub SHA.
This module lets a run install it from the developer's LOCAL (possibly
uncommitted) amplifier-agent tree instead, so an eval run reflects work that has
not yet landed upstream.

The delivery mechanism mirrors the one the E2E harness already uses:

1. Stand up (or reuse) a long-lived Gitea container via the ``amplifier-gitea``
   CLI. This harness uses a DEDICATED name/port (``aa-eval`` / ``10120``) so it
   never collides with the E2E harness's ``aa-e2e``/``10110`` environment.
2. Ensure a ``amplifier-agent`` repo exists in that Gitea.
3. Snapshot the local working tree (committed + staged + unstaged + untracked,
   minus gitignored) into a throwaway clone and force-push it to the mirror's
   ``main`` branch, WITHOUT ever mutating the source repo.
4. Return ``{"GITEA_URL": ..., "GITEA_TOKEN": ...}``. Each task profile references
   ``${GITEA_URL}`` and declares ``token_var: GITEA_TOKEN``, and the DTU engine's
   url_rewrites (in the profile) rewrite the upstream GitHub URL to the mirror.
   Passing these as launch ``--var`` values is what activates that rewrite.

Self-contained on purpose: stdlib + subprocess only. It does NOT import the E2E
framework (``tests/e2e/framework``) so the eval harness stays independent. The
git/rsync command sequence is copied from that framework's ``snapshot_push`` so
the two behave identically.

On ANY failure this raises ``LocalMirrorError`` -- it never silently falls back
to the upstream pinned SHA, because a silent fallback would make an eval run lie
about which code it tested.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Dedicated Gitea coordinates for the eval harness. Kept distinct from the E2E
# harness's aa-e2e/10110 so the two can run side by side without colliding.
GITEA_NAME = "aa-eval"
GITEA_PORT = 10120
MIRROR_REPO = "amplifier-agent"


class LocalMirrorError(RuntimeError):
    """Raised when a gitea/git subprocess fails or returns unexpected output."""


def _run(
    argv: list[str], *, cwd: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing text output. Raises LocalMirrorError on failure."""
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    if check and proc.returncode != 0:
        raise LocalMirrorError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\nstderr:\n{proc.stderr}"
        )
    return proc


def _run_json(argv: list[str], *, cwd: str | None = None) -> Any:
    """Run a command and parse its stdout as JSON."""
    proc = _run(argv, cwd=cwd)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LocalMirrorError(f"expected JSON from {' '.join(argv)}, got:\n{proc.stdout}") from exc


def _q(value: str) -> str:
    """Minimal shell quoting for paths embedded in a bash -c pipeline."""
    return "'" + value.replace("'", "'\\''") + "'"


def _ensure_gitea(name: str = GITEA_NAME, port: int = GITEA_PORT) -> dict[str, Any]:
    """Ensure a running Gitea container named ``name`` exists; return coordinates.

    Reuses an existing running container with a matching name, else creates one.
    Always mints a fresh token (Gitea does not store token values) and reads the
    current mapped port from ``status``.

    Returns a dict with keys ``id``, ``port``, ``token``.
    """
    entries = _run_json(["amplifier-gitea", "list"])
    found: dict[str, Any] | None = None
    if isinstance(entries, list):
        for entry in entries:
            if entry.get("name") == name and entry.get("container_running"):
                found = entry
                break

    if found is not None:
        print(f"[local-mirror] gitea: reusing running container '{name}' (id={found['id']})")
        match: dict[str, Any] = found
    else:
        print(
            f"[local-mirror] gitea: creating container '{name}' on port {port} "
            "(pulls image on first run)..."
        )
        match = _run_json(["amplifier-gitea", "create", "--port", str(port), "--name", name])
        print(f"[local-mirror] gitea: created (id={match['id']})")

    gitea_id = match["id"]

    # Read authoritative mapped port from status.
    status = _run_json(["amplifier-gitea", "status", gitea_id])
    resolved_port = status.get("port", match.get("port", port))

    token_info = _run_json(["amplifier-gitea", "token", gitea_id])
    token = token_info["token"]

    return {"id": gitea_id, "port": resolved_port, "token": token}


def _ensure_repo(gitea_port: int, token: str, repo: str) -> None:
    """Create the Gitea repo if it does not already exist (ignore 409 conflicts)."""
    url = f"http://localhost:{gitea_port}/api/v1/user/repos"
    payload = json.dumps(
        {"name": repo, "private": False, "auto_init": False, "default_branch": "main"}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return  # already exists
        raise LocalMirrorError(
            f"gitea repo create failed ({exc.code}) for {repo}: "
            f"{exc.read().decode(errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LocalMirrorError(f"gitea repo create request failed for {repo}: {exc}") from exc


def _snapshot_push(local_repo_path: str, gitea_port: int, token: str, repo: str) -> None:
    """Force-push a snapshot of the local working tree to the Gitea repo.

    Implements the gitea-skill snapshot pattern WITHOUT mutating the source repo:

    1. ``git clone --local`` the source into a temp dir (fast, hardlink-free).
    2. Overlay the working set (cached + modified + untracked, minus gitignored)
       into the clone via rsync.
    3. Delete files that are tracked-but-deleted in the source.
    4. Commit (allow-empty) and force-push HEAD to refs/heads/main.

    Raises LocalMirrorError on ANY failure -- never falls back to the source tree.
    """
    print(f"[local-mirror] gitea: pushing working-tree snapshot of {repo}...")
    src = str(Path(local_repo_path).expanduser().resolve())
    snap_dir = tempfile.mkdtemp(prefix=f"aa-eval-snap-{repo}-")
    snap = str(Path(snap_dir) / "repo")

    try:
        _run(["git", "clone", "--local", "--no-hardlinks", src, snap])

        # Overlay the exact working set into the clone. ls-files -z gives NUL-delimited
        # paths; rsync --files-from=- --from0 reads that list. Run as one bash pipeline.
        overlay = (
            f"git -C {_q(src)} ls-files -z --cached --modified --others --exclude-standard "
            f"| rsync -a --files-from=- --from0 {_q(src)}/ {_q(snap)}/"
        )
        _run(["bash", "-c", overlay])

        # Remove files deleted in the working tree but still tracked.
        deleted = _run(["git", "-C", src, "ls-files", "-z", "--deleted"])
        for rel in filter(None, deleted.stdout.split("\0")):
            target = Path(snap) / rel
            target.unlink(missing_ok=True)

        # Commit the snapshot inside the clone (never the source).
        _run(
            [
                "git",
                "-C",
                snap,
                "-c",
                "user.email=snapshot@local",
                "-c",
                "user.name=Snapshot",
                "add",
                "-A",
            ]
        )
        _run(
            [
                "git",
                "-C",
                snap,
                "-c",
                "user.email=snapshot@local",
                "-c",
                "user.name=Snapshot",
                "commit",
                "--allow-empty",
                "-m",
                "working-tree snapshot",
            ]
        )
        push_url = f"http://admin:{token}@localhost:{gitea_port}/admin/{repo}.git"
        _run(
            [
                "git",
                "-C",
                snap,
                "-c",
                "credential.helper=",
                "push",
                "--force",
                push_url,
                "HEAD:refs/heads/main",
            ]
        )
    finally:
        shutil.rmtree(snap_dir, ignore_errors=True)


def ensure_local_mirror(repo_path: Path | str) -> dict[str, str]:
    """Mirror the LOCAL amplifier-agent working tree into Gitea; return launch vars.

    Stands up (or reuses) the dedicated ``aa-eval`` Gitea, ensures the
    ``amplifier-agent`` repo exists, and force-pushes a snapshot of ``repo_path``'s
    working tree (committed + staged + unstaged + untracked, minus gitignored)
    without mutating the source.

    Args:
        repo_path: Path to the local amplifier-agent repo to mirror.

    Returns:
        ``{"GITEA_URL": "http://localhost:<port>", "GITEA_TOKEN": "<token>"}``.
        These names matter: task profiles reference ``${GITEA_URL}`` and declare
        ``token_var: GITEA_TOKEN``, and passing them as DTU launch ``--var`` values
        activates the profile's url_rewrites so the agent is installed from the
        mirror instead of the upstream pinned SHA.

    Raises:
        LocalMirrorError: on any gitea/git failure. Never silently falls back to
            the upstream pinned SHA.
    """
    src = Path(repo_path).expanduser().resolve()
    if not (src / ".git").exists():
        raise LocalMirrorError(f"not a git repo (no .git): {src}")

    gitea = _ensure_gitea()
    port = int(gitea["port"])
    token = str(gitea["token"])

    _ensure_repo(port, token, MIRROR_REPO)
    _snapshot_push(str(src), port, token, MIRROR_REPO)

    gitea_url = f"http://localhost:{port}"
    print(f"[local-mirror] mirrored {src} -> {gitea_url}")
    return {"GITEA_URL": gitea_url, "GITEA_TOKEN": token}


def teardown_local_mirror(name: str = GITEA_NAME) -> None:
    """Destroy every Gitea env named ``name`` (best-effort; never raises).

    Called at the end of an eval run to clean up the dedicated ``aa-eval`` Gitea
    the harness stood up. Multiple envs can share a name, so this lists all envs
    and destroys each match. Teardown must NEVER fail a run: any error while
    listing or destroying is swallowed with a concise warning line.

    Args:
        name: Gitea env name to tear down (defaults to ``GITEA_NAME``).
    """
    try:
        entries = _run_json(["amplifier-gitea", "list"])
    except Exception as exc:  # noqa: BLE001 - teardown must never raise
        print(f"[local-mirror] warning: could not list gitea envs to tear down: {exc}")
        return

    matches = (
        [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == name]
        if isinstance(entries, list)
        else []
    )

    if not matches:
        print(f"[local-mirror] no gitea env '{name}' to tear down")
        return

    for entry in matches:
        env_id = entry.get("id")
        try:
            _run(["amplifier-gitea", "destroy", str(env_id)])
            print(f"[local-mirror] torn down gitea env '{name}' ({env_id})")
        except Exception as exc:  # noqa: BLE001 - best-effort; keep going
            print(f"[local-mirror] warning: failed to destroy gitea env '{name}' ({env_id}): {exc}")


__all__ = [
    "ensure_local_mirror",
    "teardown_local_mirror",
    "LocalMirrorError",
    "GITEA_NAME",
    "GITEA_PORT",
]
