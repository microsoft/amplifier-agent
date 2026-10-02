"""Whether the installed amplifier-agent is the code the run meant to test."""

import functools
import subprocess
import tomllib
from typing import Any

from amplifier_agent_evaluations import REPO_ROOT

# The packages each surface's install.sh records whose commit must match; other recorded packages are informational.
PACKAGES = {
    "python": ("amplifier-agent", "amplifier-agent-engine"),
    "typescript": ("amplifier-agent-ts",),
    "http": ("amplifier-agent-http", "amplifier-agent", "amplifier-agent-engine"),
}
UPSTREAM = "https://github.com/microsoft/amplifier-agent"
# The release tag the checkout's packages and install commands pin; profiles install from it.
TAG = "v" + tomllib.loads((REPO_ROOT / "packages/python/pyproject.toml").read_text())["project"]["version"]


@functools.cache
def github_tag_commit(url: str = UPSTREAM, tag: str = TAG) -> str:
    """The commit `git ls-remote` reports for the tag, peeled when annotated, once per process."""
    out = subprocess.run(
        ["git", "ls-remote", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    refs = {ref: sha for sha, ref in (line.split("\t") for line in out.splitlines())}
    sha = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if sha is None:
        raise RuntimeError(f"git ls-remote {url} found no tag {tag}")
    return sha


def installed_commits(installed: dict[str, Any], surface: str = "python") -> dict[str, str | None]:
    """Each of the surface's packages' commit: `commit` for a package built from a clone, else its vcs direct_url."""
    commits: dict[str, str | None] = {}
    for name in PACKAGES[surface]:
        package = (installed.get("packages") or {}).get(name) or {}
        vcs = ((package.get("direct_url") or {}).get("vcs_info")) or {}
        commits[name] = package.get("commit") or vcs.get("commit_id")
    return commits


def _common_commit(installed: dict[str, Any], surface: str) -> tuple[str | None, str | None]:
    """(the one commit every package reports, or None with the reason)."""
    commits = installed_commits(installed, surface)
    missing = [name for name, sha in commits.items() if not sha]
    if missing:
        return None, f"no vcs commit_id for {', '.join(missing)}"
    if len(set(commits.values())) != 1:
        return None, "packages report different commits: " + ", ".join(f"{k}={v}" for k, v in commits.items())
    return next(iter(commits.values())), None


def verdict(installed: dict[str, Any], install: str, expected: str, surface: str = "python") -> dict[str, Any]:
    """Every package of the surface must report `expected`: the ls-remote sha for github, the snapshot HEAD for
    checkout."""
    commit, problem = _common_commit(installed, surface)
    if problem is None and installed.get("surface", surface) != surface:
        problem = f"installed.json is for surface {installed.get('surface')!r}, the task is {surface!r}"
    ok = problem is None and commit == expected
    reason = problem or ("installed commit matches" if ok else f"installed {commit} != expected {expected}")
    return {
        "surface": surface,
        "install": install,
        "installed_commit": commit,
        "expected": expected,
        "ok": ok,
        "reason": reason,
    }
