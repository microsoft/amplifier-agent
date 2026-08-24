"""Admin command: update — wraps the canonical uv-tool reinstall in one verb.

Replaces the long-hand ritual:

    uv tool install --reinstall --force "git+https://github.com/microsoft/amplifier-agent@v<tag>"

with a single command that detects the install method, checks GitHub Releases
for the latest tag, and runs the install (or refuses if the install method
would clobber a developer's editable checkout).

Refer to the `self-managing-tool-patterns` skill for the broader pattern.
This implementation is a narrower slice of that pattern — no service
management, no doctor integration — focused only on the user-facing
"how do I get the latest engine?" pain point reported by consumers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any

import click

from amplifier_agent_lib import __version__

__all__ = ["update_command"]

_REPO_SLUG = "microsoft/amplifier-agent"
_RELEASES_URL = f"https://api.github.com/repos/{_REPO_SLUG}/releases/latest"
_GIT_BASE = f"https://github.com/{_REPO_SLUG}"
_PACKAGE_NAME = "amplifier-agent"


# ---------------------------------------------------------------------------
# Helpers — kept module-level so tests can monkeypatch them by attribute name.
# ---------------------------------------------------------------------------


def _current_version() -> str:
    """Return the installed engine version string."""
    return __version__


def _fetch_latest_release(url: str = _RELEASES_URL) -> dict[str, Any]:
    """Fetch the latest GitHub Release payload.

    Raises on network failure or non-2xx response. Callers must catch.
    Uses stdlib only — no new dependencies for this command.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{_PACKAGE_NAME}/{_current_version()}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _detect_install_method() -> str:
    """Return one of: 'uv-tool', 'editable', 'other'.

    Uses PEP 610 direct_url.json metadata to detect editable installs
    (per the self-managing-tool-patterns skill convention).
    Falls back to inspecting sys.executable / shutil.which() to identify
    the uv-tool layout.
    """
    try:
        dist = distribution(_PACKAGE_NAME)
        du_text = dist.read_text("direct_url.json")
        if du_text:
            du = json.loads(du_text)
            if du.get("dir_info", {}).get("editable") is True:
                return "editable"
    except PackageNotFoundError:
        pass

    # uv tool installs land the executable under ~/.local/share/uv/tools/<name>/
    # or under $UV_TOOL_DIR/<name>/. Both sys.executable and the resolved
    # `which amplifier-agent` path share that prefix.
    candidates = [sys.executable or "", shutil.which(_PACKAGE_NAME) or ""]
    for path in candidates:
        if "/uv/tools/amplifier-agent" in path or "\\uv\\tools\\amplifier-agent" in path:
            return "uv-tool"

    return "other"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a semver-ish string to a comparable tuple. Tolerates a leading 'v'."""
    s = v.lstrip("v").strip()
    parts: list[int] = []
    for piece in s.split("."):
        # Stop at first non-numeric segment (e.g. 0.5.0rc1 → (0, 5, 0)).
        num = ""
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts) if parts else (0,)


def _refresh_modules(*, output: str) -> list[str]:
    """Re-resolve floating module refs; re-prime if any branch has moved.

    Module sources float on ``@main`` by design, so they move independently of
    engine releases.  This makes ``amplifier-agent update`` able to deliver an
    upstream module fix without a new engine version -- previously the only
    route to one was an engine release that existed solely to move the cache.

    Costs nothing when nothing has changed: resolution is ``git ls-remote``
    (refs only, no repository data, no authentication) and the comparison reads
    metadata foundation already wrote beside each clone.  Downloads happen only
    for repositories that actually moved, and only via the re-prime below.

    Never raises.  Any failure -- offline, git missing, unreadable cache --
    leaves the install exactly as it was, which is the same state a user who
    never ran ``update`` would be in.

    Returns:
        Repository URLs whose branch moved.  Empty when nothing changed, when
        resolution could not run, or when no clones exist yet.
    """
    try:
        import asyncio

        from amplifier_agent_lib import __version__ as _version
        from amplifier_agent_lib.bundle import BUNDLE_MD
        from amplifier_agent_lib.bundle.cache import cache_dir_for_version
        from amplifier_agent_lib.bundle.pinning import find_drifted_modules, resolve_floating_refs
        from amplifier_agent_lib.foundation_home import module_cache_root

        async def _resolve() -> dict[str, tuple[str, str]]:
            from amplifier_foundation import load_bundle

            # Parse only -- no prepare(), so nothing is cloned or installed.
            bundle = await load_bundle(f"file://{BUNDLE_MD}")
            pin = await resolve_floating_refs(bundle.to_mount_plan(), clone_root=module_cache_root())
            return find_drifted_modules(pin.pins, module_cache_root())

        drifted = asyncio.run(_resolve())
    except Exception as exc:  # pragma: no cover - defensive; never block update
        if output != "json":
            click.echo(f"Module refresh skipped ({exc.__class__.__name__}).")
        return []

    if not drifted:
        if output != "json":
            click.echo("Modules are current.")
        return []

    if output != "json":
        click.echo(f"{len(drifted)} module(s) have upstream changes:")
        for url in sorted(drifted):
            _old, new = drifted[url]
            click.echo(f"  {url.rsplit('/', 1)[-1]} -> {new[:12]}")

    # Drop the prepared-bundle artefact so the next prepare is cold and picks up
    # the new commits. Only the artefact is removed -- every module clone stays
    # exactly where it is, and the ones that did not move are reused verbatim.
    try:
        cache_dir = cache_dir_for_version(_version)
        manifest = cache_dir / "manifest.json"
        manifest.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - defensive
        if output != "json":
            click.echo(f"Could not invalidate prepared cache ({exc}); next run will still use the old modules.")
        return sorted(drifted)

    if output != "json":
        click.echo("Re-priming bundle cache...")
    subprocess.run(["amplifier-agent-post-install"], check=False)
    return sorted(drifted)


def _build_install_cmd(tag: str) -> list[str]:
    """Build the uv tool install argv for the given ref."""
    return [
        "uv",
        "tool",
        "install",
        "--reinstall",
        "--force",
        f"git+{_GIT_BASE}@{tag}",
    ]


def _format_status_table(current: str, latest: str, tag: str, install_method: str, release_date: str) -> str:
    """Render the human-readable status block."""
    return (
        "Checking latest amplifier-agent release...\n"
        f"  Current:  {current}\n"
        f"  Latest:   {latest}  ({tag} from {release_date})\n"
        f"  Install:  {install_method}\n"
    )


def _emit(payload: dict[str, Any], output: str, human_lines: list[str]) -> None:
    """Emit either the JSON envelope or the accumulated human lines."""
    if output == "json":
        click.echo(json.dumps(payload))
    else:
        for line in human_lines:
            click.echo(line)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(name="update")
@click.option("--check", "check_only", is_flag=True, default=False, help="Show status only; do not install.")
@click.option("--tag", "tag_override", type=str, default=None, help="Install a specific tag/branch/SHA.")
@click.option("--force", "force", is_flag=True, default=False, help="Reinstall even when versions match.")
@click.option(
    "--output",
    "output",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def update_command(check_only: bool, tag_override: str | None, force: bool, output: str) -> None:
    """Check for and install the latest amplifier-agent release.

    Wraps `uv tool install --reinstall --force git+...@<tag>` behind a single
    command, with install-method detection so we don't clobber editable dev
    checkouts.
    """
    current = _current_version()
    install_method = _detect_install_method()

    # --- Step 1: resolve the target ref --------------------------------------
    # --tag short-circuits the GitHub API call entirely. Empty string is rejected.
    if tag_override is not None:
        if tag_override.strip() == "":
            click.echo("Error: --tag requires a non-empty ref.", err=True)
            sys.exit(2)
        tag = tag_override
        latest_version = tag.lstrip("v")
        release_url = f"{_GIT_BASE}/tree/{tag}"
        release_date = "n/a (--tag override)"
    else:
        try:
            release = _fetch_latest_release()
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
            err_lines = [
                f"Error: couldn't reach GitHub Releases API: {exc}",
                "Manual install:",
                f'  uv tool install --reinstall --force "git+{_GIT_BASE}@<tag-you-want>"',
            ]
            if output == "json":
                payload: dict[str, Any] = {
                    "current": current,
                    "latest": None,
                    "tag": None,
                    "release_url": None,
                    "install_method": install_method,
                    "action": "failed",
                    "error": f"github-api-unreachable: {exc}",
                }
                click.echo(json.dumps(payload))
            else:
                for line in err_lines:
                    click.echo(line, err=True)
            sys.exit(2)
        tag = release.get("tag_name") or ""
        latest_version = tag.lstrip("v") or "unknown"
        release_url = release.get("html_url") or f"{_GIT_BASE}/releases/tag/{tag}"
        release_date = release.get("published_at") or "unknown"

    # --- Step 2: decide the action ------------------------------------------
    # Default state — overwritten by the branches below.
    action = "skipped_up_to_date"
    error: str | None = None

    versions_match = _parse_version(current) == _parse_version(latest_version)
    needs_install = (not versions_match) or force or (tag_override is not None and force)

    if check_only:
        action = "checked"
    elif install_method == "editable":
        action = "skipped_editable"
    elif install_method == "other":
        action = "skipped_other"
    elif needs_install:
        action = "updating"
    else:
        action = "skipped_up_to_date"

    payload = {
        "current": current,
        "latest": latest_version,
        "tag": tag,
        "release_url": release_url,
        "install_method": install_method,
        "action": action,
        "error": error,
    }
    human_lines = [_format_status_table(current, latest_version, tag, install_method, release_date).rstrip()]

    # --- Step 3: execute the action -----------------------------------------
    if check_only:
        _emit(payload, output, human_lines)
        sys.exit(0)

    if install_method == "editable":
        # Find the editable source dir for the human message.
        try:
            dist = distribution(_PACKAGE_NAME)
            du = json.loads(dist.read_text("direct_url.json") or "{}")
            src_path = du.get("url", "<unknown>")
        except (PackageNotFoundError, json.JSONDecodeError, TypeError):
            src_path = "<unknown>"
        msg = (
            f"Detected editable install at `{src_path}`. `update` is for `uv tool` installs only.\n"
            "To pull latest in your dev checkout: `git pull && uv sync`."
        )
        if output == "json":
            payload["error"] = "editable-install-refused"
            click.echo(json.dumps(payload))
        else:
            human_lines.append(msg)
            for line in human_lines:
                click.echo(line, err=True)
        sys.exit(2)

    if install_method == "other":
        cmd_str = " ".join(_build_install_cmd(tag))
        msg = (
            "Detected a non-uv install. Equivalent manual command:\n"
            f"  {cmd_str}\n"
            "If you want this command to run updates directly, install via `uv tool install`."
        )
        if output == "json":
            click.echo(json.dumps(payload))
        else:
            human_lines.append(msg)
            for line in human_lines:
                click.echo(line)
        sys.exit(0)

    # install_method == "uv-tool"
    if not needs_install:
        # The engine is current, but modules are not tied to engine releases:
        # every module source floats on `@main`, so an upstream provider fix can
        # land at any time without a new amplifier-agent version. Exiting here
        # -- which is what this branch used to do -- meant `update` had no way to
        # deliver one, and the only route to a module fix was an engine release
        # nobody needed. Check for module drift before reporting "up to date".
        drift = _refresh_modules(output=output)
        msg = "Already up to date. Use --force to reinstall."
        payload["modules_updated"] = drift
        if output == "json":
            click.echo(json.dumps(payload))
        else:
            human_lines.append(msg)
            for line in human_lines:
                click.echo(line)
        sys.exit(0)

    cmd = _build_install_cmd(tag)
    if output != "json":
        click.echo(human_lines[0])
        click.echo(f"Running: {' '.join(cmd)}")
    completed = subprocess.run(cmd, check=False)

    if completed.returncode == 0:
        payload["action"] = "updated"
        if output == "json":
            click.echo(json.dumps(payload))
        else:
            click.echo(f"\u2713 amplifier-agent updated to {tag}")
            click.echo("Priming bundle cache...")

        # post-install is idempotent and always exits 0; failures log to
        # stderr and never block the update.
        subprocess.run(["amplifier-agent-post-install"], check=False)

        sys.exit(0)

    payload["action"] = "failed"
    payload["error"] = f"uv-tool-install-exit-{completed.returncode}"
    if output == "json":
        click.echo(json.dumps(payload))
    else:
        click.echo(
            f"Error: `uv tool install` exited with code {completed.returncode}.",
            err=True,
        )
        click.echo("Manual install:", err=True)
        click.echo(f'  uv tool install --reinstall --force "git+{_GIT_BASE}@{tag}"', err=True)
    sys.exit(2)
