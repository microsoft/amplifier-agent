"""Whether the installed amplifier-agent is the code the run meant to test."""

import functools
import json
import os
import subprocess
import tomllib
from typing import Any
import urllib.request

from amplifier_agent_evaluations import REPO_ROOT

# The packages each surface's install.sh records whose identity must match; other recorded packages are informational.
PACKAGES = {
    "python": ("amplifier-agent", "amplifier-agent-engine"),
    "typescript": ("amplifier-agent-ts",),
    "http": ("amplifier-agent-http", "amplifier-agent", "amplifier-agent-engine"),
}
UPSTREAM = "https://github.com/microsoft/amplifier-agent"
# The release tag the checkout's packages and install commands pin; profiles install from it.
TAG = "v" + tomllib.loads((REPO_ROOT / "packages/python/pyproject.toml").read_text())["project"]["version"]
RELEASES = "https://api.github.com/repos/microsoft/amplifier-agent/releases/tags/"
# The TypeScript package archive the release carries, which is the tarball npm serves for the version.
TYPESCRIPT_ASSET = f"amplifier-agent-ts-{TAG.removeprefix('v')}.tgz"


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


@functools.cache
def github_release_digest(asset: str = TYPESCRIPT_ASSET, tag: str = TAG) -> str:
    """The `sha256:<hex>` digest GitHub reports for the release asset, once per process."""
    request = urllib.request.Request(RELEASES + tag, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    digest = next((entry.get("digest") for entry in release.get("assets") or [] if entry.get("name") == asset), None)
    if not digest:
        raise RuntimeError(f"release {tag} has no digest for asset {asset}")
    return digest


def installed_identities(installed: dict[str, Any], surface: str = "python") -> dict[str, str | None]:
    """Each of the surface's packages' identity: `commit` for a package built from a clone, else its vcs direct_url's
    commit, else `sha256:<tarball_sha256>` for a package archive npm installed."""
    identities: dict[str, str | None] = {}
    for name in PACKAGES[surface]:
        package = (installed.get("packages") or {}).get(name) or {}
        vcs = ((package.get("direct_url") or {}).get("vcs_info")) or {}
        tarball = package.get("tarball_sha256")
        identities[name] = package.get("commit") or vcs.get("commit_id") or (f"sha256:{tarball}" if tarball else None)
    return identities


def _common_identity(installed: dict[str, Any], surface: str) -> tuple[str | None, str | None]:
    """(the one identity every package reports, or None with the reason)."""
    identities = installed_identities(installed, surface)
    missing = [name for name, identity in identities.items() if not identity]
    if missing:
        return None, f"no commit or tarball digest for {', '.join(missing)}"
    if len(set(identities.values())) != 1:
        return None, "packages report different identities: " + ", ".join(f"{k}={v}" for k, v in identities.items())
    return next(iter(identities.values())), None


def verdict(installed: dict[str, Any], install: str, expected: str, surface: str = "python") -> dict[str, Any]:
    """Every package of the surface must report `expected`: on github the release's package archive digest for
    TypeScript and the ls-remote sha for the rest, on checkout the snapshot HEAD."""
    identity, problem = _common_identity(installed, surface)
    if problem is None and installed.get("surface", surface) != surface:
        problem = f"installed.json is for surface {installed.get('surface')!r}, the task is {surface!r}"
    ok = problem is None and identity == expected
    reason = problem or ("installed identity matches" if ok else f"installed {identity} != expected {expected}")
    return {
        "surface": surface,
        "install": install,
        "installed_identity": identity,
        "expected": expected,
        "ok": ok,
        "reason": reason,
    }
