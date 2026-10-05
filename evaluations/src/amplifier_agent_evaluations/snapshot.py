"""The checkout snapshot Digital Twin Universe serves as github.com/microsoft/amplifier-agent."""

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from amplifier_agent_evaluations import EVAL_ROOT, REPO_ROOT
from amplifier_agent_evaluations.provenance import TAG

SNAPSHOT = EVAL_ROOT / ".snapshot" / "amplifier-agent"
# Not served: the evaluations hold the rubrics and answer keys, which the agent under test must not be able to read.
EXCLUDED = ("evaluations",)
# A fixed identity and date, and no user or system git config, so the same files always commit to the same HEAD and
# an image baked for that HEAD can be reused.
COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "amplifier-agent-evaluations",
    "GIT_AUTHOR_EMAIL": "evaluations@amplifier-agent.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "amplifier-agent-evaluations",
    "GIT_COMMITTER_EMAIL": "evaluations@amplifier-agent.invalid",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout


def create(
    repo: Path = REPO_ROOT, destination: Path = SNAPSHOT, excluded: tuple[str, ...] = EXCLUDED
) -> dict[str, Any]:
    """A single-commit repository, tagged with the release tag, holding the working tree's files, minus gitignored ones
    and those under `excluded`. The same files give the same HEAD."""
    listed = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=repo).split("\0")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    copied = 0
    for relative in sorted(set(filter(None, listed))):
        if Path(relative).parts[0] in excluded:
            continue
        source = repo / relative
        if source.is_symlink() or source.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
            copied += 1
    env = os.environ | COMMIT_ENV
    _git("init", "--quiet", "--initial-branch", "main", cwd=destination, env=env)
    _git("add", "--all", "--force", cwd=destination, env=env)
    _git(
        "commit",
        "--quiet",
        "--no-verify",
        "--allow-empty",
        "-m",
        "working tree snapshot",
        cwd=destination,
        env=env,
    )
    _git("tag", TAG, cwd=destination, env=env)
    return {"repo": str(repo), "files": copied, "head": _git("rev-parse", "HEAD", cwd=destination).strip()}
