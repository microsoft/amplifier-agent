"""The checkout snapshot dtu-lite serves as github.com/microsoft/amplifier-agent."""

from pathlib import Path
import shutil
import subprocess
from typing import Any

from amplifier_agent_evaluations import EVAL_ROOT, REPO_ROOT
from amplifier_agent_evaluations.provenance import BRANCH

SNAPSHOT = EVAL_ROOT / ".snapshot" / "amplifier-agent"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def create(repo: Path = REPO_ROOT, destination: Path = SNAPSHOT) -> dict[str, Any]:
    """A single-commit repository on the install branch holding the working tree's files, minus gitignored ones."""
    listed = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=repo).split("\0")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    copied = 0
    for relative in sorted(set(filter(None, listed))):
        source = repo / relative
        if source.is_symlink() or source.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
            copied += 1
    _git("init", "--quiet", "--initial-branch", BRANCH, cwd=destination)
    _git("add", "--all", "--force", cwd=destination)
    _git(
        "-c",
        "user.name=amplifier-agent-evaluations",
        "-c",
        "user.email=evaluations@amplifier-agent.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--no-verify",
        "--allow-empty",
        "-m",
        "working tree snapshot",
        cwd=destination,
    )
    return {"repo": str(repo), "files": copied, "head": _git("rev-parse", "HEAD", cwd=destination).strip()}
