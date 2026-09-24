"""Whether the installed amplifier-agent is the code the run meant to test."""

import functools
import subprocess
from typing import Any

PACKAGES = ("amplifier-agent", "amplifier-agent-engine")
UPSTREAM = "https://github.com/microsoft/amplifier-agent"
BRANCH = "v1"


@functools.cache
def github_head(url: str = UPSTREAM, branch: str = BRANCH) -> str:
    """The sha `git ls-remote` reports for the branch, once per process."""
    out = subprocess.run(
        ["git", "ls-remote", url, f"refs/heads/{branch}"], check=True, capture_output=True, text=True, timeout=60
    ).stdout.split()
    if not out:
        raise RuntimeError(f"git ls-remote {url} refs/heads/{branch} returned nothing")
    return out[0]


def installed_commits(installed: dict[str, Any]) -> dict[str, str | None]:
    commits: dict[str, str | None] = {}
    for name in PACKAGES:
        package = (installed.get("packages") or {}).get(name) or {}
        vcs = ((package.get("direct_url") or {}).get("vcs_info")) or {}
        commits[name] = vcs.get("commit_id")
    return commits


def _common_commit(installed: dict[str, Any]) -> tuple[str | None, str | None]:
    """(the one commit every package reports, or None with the reason)."""
    commits = installed_commits(installed)
    missing = [name for name, sha in commits.items() if not sha]
    if missing:
        return None, f"no vcs commit_id for {', '.join(missing)}"
    if len(set(commits.values())) != 1:
        return None, "packages report different commits: " + ", ".join(f"{k}={v}" for k, v in commits.items())
    return next(iter(commits.values())), None


def verdict(installed: dict[str, Any], install: str, expected: str) -> dict[str, Any]:
    """Every package must report `expected`: the ls-remote sha for github, the snapshot HEAD for checkout."""
    commit, problem = _common_commit(installed)
    ok = problem is None and commit == expected
    reason = problem or ("installed commit matches" if ok else f"installed {commit} != expected {expected}")
    return {"install": install, "installed_commit": commit, "expected": expected, "ok": ok, "reason": reason}
