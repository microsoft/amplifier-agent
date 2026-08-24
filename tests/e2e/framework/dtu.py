"""Self-contained subprocess wrappers for amplifier-gitea and amplifier-digital-twin.

Deliberately has NO dependency on the amplifier-tester bundle. Every shell-out uses
``subprocess.run(capture_output=True, text=True)`` and parses JSON from stdout. Field
names follow the amplifier-gitea and amplifier-digital-twin CLI contracts exactly.

Two responsibilities:

* **Gitea mirror** — stand up (or reuse) one long-lived Gitea container and force-push
  a snapshot of the local working tree (committed + staged + unstaged + untracked,
  minus gitignored) WITHOUT ever mutating the source repo. A ``--repo NAME@REF`` extra
  pushes that committed ref instead (``snapshot_push_ref``), under the same rule: every
  git command runs in a throwaway clone, never in the user's checkout.
* **DTU lifecycle** — launch / poll-readiness / exec / update / destroy a Digital Twin
  instance.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

from .progress import log

# Repos that may need mirroring. amplifier-agent is ALWAYS mirrored; the other two
# only when their working tree is dirty.
CANDIDATE_REPOS = ["amplifier-agent", "amplifier-core", "amplifier-foundation"]

# Owner assumed when a --repo value names a bare repo.
DEFAULT_REPO_OWNER = "microsoft"


class DTUError(RuntimeError):
    """Raised when a gitea/DTU subprocess fails or returns unexpected output."""


class RepoSpec(NamedTuple):
    """One resolved ``--repo`` value: which repo, and optionally which ref."""

    owner: str
    name: str
    ref: str | None

    @property
    def key(self) -> str:
        """Canonical ``owner/name[@ref]`` form, used for state comparison."""
        return f"{self.owner}/{self.name}" + (f"@{self.ref}" if self.ref else "")

    @property
    def github_url(self) -> str:
        """The upstream URL this repo is redirected away from (and cloned from)."""
        return f"https://github.com/{self.owner}/{self.name}"


def parse_repo_spec(spec: str) -> RepoSpec:
    """Parse ``[owner/]name[@ref]`` into a RepoSpec, defaulting the owner to microsoft.

    Split on the LAST ``@`` rather than the first: ``@`` is not valid in a repo name, so
    everything before the last one is the repo. That keeps refs containing ``/`` intact
    (``amplifier-bundle-skills@feature/x``) and keeps an ``owner/repo`` prefix from ever
    being mistaken for a ref.
    """
    text = spec.strip()
    if not text:
        raise DTUError("empty --repo value; expected NAME[@REF] or OWNER/NAME[@REF]")

    base, at, ref = text.rpartition("@")
    if not at:
        base, ref = text, ""
    elif not base or not ref:
        raise DTUError(f"invalid --repo value {spec!r}; expected NAME[@REF] or OWNER/NAME[@REF]")

    owner, slash, name = base.partition("/")
    if not slash:
        owner, name = DEFAULT_REPO_OWNER, base
    if not owner or not name or "/" in name:
        raise DTUError(f"invalid --repo value {spec!r}; expected NAME[@REF] or OWNER/NAME[@REF]")

    return RepoSpec(owner=owner, name=name, ref=ref or None)


def _run(argv: list[str], *, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing text output. Raises DTUError on non-zero when check."""
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    if check and proc.returncode != 0:
        raise DTUError(f"command failed ({proc.returncode}): {' '.join(argv)}\nstderr:\n{proc.stderr}")
    return proc


def _run_json(argv: list[str], *, cwd: str | None = None) -> Any:
    """Run a command and parse its stdout as JSON."""
    proc = _run(argv, cwd=cwd)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DTUError(f"expected JSON from {' '.join(argv)}, got:\n{proc.stdout}") from exc


# --------------------------------------------------------------------------- #
# Gitea (amplifier-gitea)
# --------------------------------------------------------------------------- #


def ensure_gitea(name: str = "aa-e2e", port: int = 10110) -> dict[str, Any]:
    """Ensure a running Gitea container named ``name`` exists; return its coordinates.

    Reuses an existing running container with a matching name, else creates one.
    Always mints a fresh token (Gitea does not store token values) and reads the
    current mapped port from ``status``.

    Returns:
        dict with keys ``id``, ``port``, ``token``, ``gitea_url``.
    """
    entries = _run_json(["amplifier-gitea", "list"])
    found: dict[str, Any] | None = None
    if isinstance(entries, list):
        for entry in entries:
            if entry.get("name") == name and entry.get("container_running"):
                found = entry
                break

    if found is not None:
        log(f"gitea: reusing running container '{name}' (id={found['id']})")
        match: dict[str, Any] = found
    else:
        log(f"gitea: creating container '{name}' on port {port} (pulls image on first run)...")
        match = _run_json(["amplifier-gitea", "create", "--port", str(port), "--name", name])
        log(f"gitea: created (id={match['id']})")

    gitea_id = match["id"]

    # Read authoritative mapped port from status.
    status = _run_json(["amplifier-gitea", "status", gitea_id])
    resolved_port = status.get("port", match.get("port", port))

    token_info = _run_json(["amplifier-gitea", "token", gitea_id])
    token = token_info["token"]

    return {
        "id": gitea_id,
        "port": resolved_port,
        "token": token,
        "gitea_url": f"http://localhost:{resolved_port}",
    }


def ensure_repo(gitea_port: int, token: str, repo: str) -> None:
    """Create the Gitea repo if it does not already exist (ignore 409 conflicts)."""
    url = f"http://localhost:{gitea_port}/api/v1/user/repos"
    payload = json.dumps({"name": repo, "private": False, "auto_init": False, "default_branch": "main"}).encode("utf-8")
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
        raise DTUError(
            f"gitea repo create failed ({exc.code}) for {repo}: {exc.read().decode(errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DTUError(f"gitea repo create request failed for {repo}: {exc}") from exc


def snapshot_push(local_repo_path: str, gitea_port: int, token: str, repo: str) -> None:
    """Force-push a snapshot of the local working tree to the Gitea repo.

    Implements the gitea-skill snapshot pattern WITHOUT mutating the source repo:

    1. ``git clone --local`` the source into a temp dir (fast, hardlink-free).
    2. Overlay the working set (cached + modified + untracked, minus gitignored)
       into the clone via rsync.
    3. Delete files that are tracked-but-deleted in the source.
    4. Commit (allow-empty) and force-push HEAD to refs/heads/main.

    Raises DTUError on ANY failure — never falls back to the source tree.
    """
    log(f"gitea: pushing working-tree snapshot of {repo}...")
    src = str(Path(local_repo_path).expanduser().resolve())
    snap_dir = tempfile.mkdtemp(prefix=f"aa-e2e-snap-{repo}-")
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


def snapshot_push_ref(
    local_repo_path: str | None,
    github_url: str,
    ref: str,
    gitea_port: int,
    token: str,
    repo: str,
) -> None:
    """Force-push one COMMITTED ref to the Gitea repo, ignoring any working tree.

    Covers the two ``--repo`` cases that ``snapshot_push`` cannot: an explicit ref on a
    local checkout, and a repo with no local checkout at all (cloned from GitHub).

    Like ``snapshot_push`` this NEVER mutates the source repo. When ``local_repo_path``
    is given it is cloned (``--local --no-hardlinks``) into a temp dir and every
    resolve/fetch runs INSIDE that clone, whose ``origin`` is the local path -- so no git
    command ever runs against the user's checkout. If the ref is not obtainable from
    there, it is fetched from ``github_url`` into the clone instead.

    The commit lands as ``refs/heads/main`` in the mirror, which is what makes a
    ``@main`` reference inside the DTU resolve to it whatever the ref was named.

    Raises DTUError when the ref cannot be resolved or the push fails.
    """
    origin = str(Path(local_repo_path).expanduser().resolve()) if local_repo_path else github_url
    source = "local checkout" if local_repo_path else "GitHub"
    log(f"gitea: pushing {repo}@{ref} from {source} ({origin})...")
    work_dir = tempfile.mkdtemp(prefix=f"aa-e2e-ref-{repo}-")
    clone = str(Path(work_dir) / "repo")

    try:
        # --no-checkout: the commit is pushed by sha, so a working tree is dead weight.
        argv = ["git", "clone", "--no-checkout"]
        if local_repo_path:
            argv += ["--local", "--no-hardlinks"]
        _run([*argv, origin, clone])

        # The fallback is only meaningful for a local clone; without one, origin already
        # IS github_url and re-fetching the same remote would be pure duplication.
        commit = _resolve_commit(clone, ref, github_url if local_repo_path else None)
        push_url = f"http://admin:{token}@localhost:{gitea_port}/admin/{repo}.git"
        _run(
            [
                "git",
                "-C",
                clone,
                "-c",
                "credential.helper=",
                "push",
                "--force",
                push_url,
                f"{commit}:refs/heads/main",
            ]
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _resolve_commit(clone: str, ref: str, fallback_url: str | None) -> str:
    """Resolve ``ref`` to a commit sha inside ``clone``, fetching only if needed.

    Order matters. A plain ``rev-parse`` covers refs the clone already has (its default
    branch, tags), ``origin/<ref>`` covers every other branch of the source, and only
    then do we go to the network. All of it happens in the throwaway clone.
    """
    for candidate in (ref, f"origin/{ref}"):
        found = _run(["git", "-C", clone, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"], check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()

    remotes = ["origin", fallback_url] if fallback_url else ["origin"]
    for remote in remotes:
        fetched = _run(["git", "-C", clone, "fetch", "--no-tags", remote, ref], check=False)
        if fetched.returncode != 0:
            continue
        head = _run(["git", "-C", clone, "rev-parse", "--verify", "--quiet", "FETCH_HEAD^{commit}"], check=False)
        if head.returncode == 0 and head.stdout.strip():
            return head.stdout.strip()

    raise DTUError(f"ref {ref!r} could not be resolved in {clone} (origin={fallback_url or 'the cloned remote'})")


def _q(value: str) -> str:
    """Minimal shell quoting for paths embedded in a bash -c pipeline."""
    return "'" + value.replace("'", "'\\''") + "'"


def dirty_repos(workspace_root: str) -> list[str]:
    """Return candidate repos whose working tree is dirty.

    amplifier-agent is ALWAYS included regardless of cleanliness (it is the unit
    under test). The others are included only when ``git status --porcelain`` is
    non-empty and the repo directory actually exists.
    """
    root = Path(workspace_root).expanduser().resolve()
    result: list[str] = []
    for repo in CANDIDATE_REPOS:
        repo_path = root / repo
        if repo == "amplifier-agent":
            result.append(repo)
            continue
        if not (repo_path / ".git").exists():
            continue
        status = _run(["git", "-C", str(repo_path), "status", "--porcelain"], check=False)
        if status.returncode == 0 and status.stdout.strip():
            result.append(repo)
    return result


# --------------------------------------------------------------------------- #
# DTU (amplifier-digital-twin)
# --------------------------------------------------------------------------- #


def launch(profile_path: str, varmap: dict[str, str], name: str | None = None) -> dict[str, Any]:
    """Launch a DTU instance from a profile. Returns the parsed launch dict.

    The instance id is the top-level ``id`` field.
    """
    log(f"dtu: launching '{name or profile_path}' (creates container, installs amplifier-agent; ~1-2 min)...")
    argv = ["amplifier-digital-twin", "launch", profile_path]
    if name:
        argv += ["--name", name]
    for key, value in varmap.items():
        argv += ["--var", f"{key}={value}"]
    result = _run_json(argv)
    log(f"dtu: launched (id={result.get('id')})")
    return result


def check_ready(dtu_id: str) -> bool:
    """Return True if the instance is ready. Readiness is the EXIT CODE (0 ready)."""
    proc = _run(["amplifier-digital-twin", "check-readiness", dtu_id], check=False)
    return proc.returncode == 0


def wait_ready(dtu_id: str, timeout: int = 600, interval: int = 10) -> None:
    """Poll ``check_ready`` until ready or timeout. Never a single long blocking call."""
    import time

    log(f"dtu: waiting for '{dtu_id}' readiness (polling every {interval}s, timeout {timeout}s)...")
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        if check_ready(dtu_id):
            log(f"dtu: '{dtu_id}' ready after {time.monotonic() - start:.0f}s")
            return
        log(f"dtu: not ready yet ({time.monotonic() - start:.0f}s elapsed); polling again in {interval}s")
        time.sleep(interval)
    raise TimeoutError(f"DTU {dtu_id} not ready within {timeout}s")


def exec_json(dtu_id: str, argv: list[str]) -> dict[str, Any]:
    """Run a command inside the DTU. Returns ``{id, command, exit_code, stdout, stderr}``.

    For chained shell use argv=["bash", "-lc", "..."].
    """
    full = ["amplifier-digital-twin", "exec", dtu_id, "--", *argv]
    return _run_json(full)


def push_file(dtu_id: str, local_path: str, dest: str, *, recursive: bool = False) -> None:
    """Push a local file or directory into the DTU at ``dest``.

    Wraps ``amplifier-digital-twin file-push``. Parent directories are created
    automatically (file-push's ``--create-dirs`` default). Set ``recursive=True``
    when ``local_path`` is a directory.
    """
    log(f"dtu: pushing {local_path} -> {dest}")
    argv = ["amplifier-digital-twin", "file-push", dtu_id]
    if recursive:
        argv.append("--recursive")
    argv += [local_path, dest]
    _run(argv)


def update(dtu_id: str, varmap: dict[str, str]) -> dict[str, Any]:
    """Re-run the profile's update step in place (powers `refresh`)."""
    log(f"dtu: updating '{dtu_id}' in place (reinstalls from Gitea; ~1 min)...")
    argv = ["amplifier-digital-twin", "update", dtu_id]
    for key, value in varmap.items():
        argv += ["--var", f"{key}={value}"]
    result = _run_json(argv)
    log(f"dtu: updated '{dtu_id}'")
    return result


def destroy(dtu_id: str) -> None:
    """Destroy the given DTU instance."""
    log(f"dtu: destroying '{dtu_id}'...")
    _run(["amplifier-digital-twin", "destroy", dtu_id], check=False)
    log(f"dtu: destroyed '{dtu_id}'")


def list_instances() -> list[dict[str, Any]]:
    """Return the list of DTU instances."""
    result = _run_json(["amplifier-digital-twin", "list"])
    return result if isinstance(result, list) else []
