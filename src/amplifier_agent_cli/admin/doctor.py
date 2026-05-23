"""Admin command: doctor — self-diagnostic for provider, XDG paths, Python, bundle cache.

Checks (in order):
  1. Python version (>= 3.11)
  2. Provider configured (any provider env var set)
  3. XDG config home writable
  4. XDG cache home writable
  5. XDG state home writable
  6. Prepared-bundle cache present for the current version (INFO only — never causes FAIL)

Exit 0 if checks 1-5 all pass; exit 1 if any of checks 1-5 fail.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from amplifier_agent_cli.provider_detect import ProviderNotConfigured, detect_provider
from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import cache_dir_for_version

_OK: str = "[ OK ]"
_FAIL: str = "[FAIL]"
_INFO: str = "[INFO]"


@dataclass
class CacheState:
    """Represents the current state of the prepared-bundle cache."""

    status: str  # 'prepared' | 'needs prepare'
    cache_dir: Path


def check_cache_state(aaa_version: str) -> CacheState:
    """Check whether a prepared bundle exists for the given AaA version.

    Returns a :class:`CacheState` with ``status='prepared'`` if both
    ``manifest.json`` and at least one non-manifest artifact exist in the
    version-keyed cache directory; otherwise ``status='needs prepare'``.
    """
    cache_dir = cache_dir_for_version(aaa_version)
    manifest = cache_dir / "manifest.json"

    if cache_dir.exists() and manifest.exists():
        artifacts = [f for f in cache_dir.iterdir() if f.name != "manifest.json"]
        if artifacts:
            return CacheState(status="prepared", cache_dir=cache_dir)

    return CacheState(status="needs prepare", cache_dir=cache_dir)


def _check_provider() -> tuple[bool, str]:
    """Return (True, OK line) if a provider is configured, (False, FAIL line) otherwise."""
    try:
        name = detect_provider(override=None)
        return (True, f"{_OK} provider: {name}")
    except ProviderNotConfigured as exc:
        return (False, f"{_FAIL} provider: {exc.message}")


def _xdg(env_var: str, default: Path) -> Path:
    """Return XDG path from environment or the given default."""
    value = os.environ.get(env_var)
    return Path(value) if value else default


def _check_writable(label: str, path: Path) -> tuple[bool, str]:
    """Return (True, OK line) if *path* is writable; (False, FAIL line) on OSError."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor-probe"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        return (True, f"{_OK} {label}: {path}")
    except OSError as exc:
        return (False, f"{_FAIL} {label}: {path} ({exc.__class__.__name__})")


def _check_python_version() -> tuple[bool, str]:
    """Return (True, OK line) if Python >= 3.11; (False, FAIL line) otherwise."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    micro = sys.version_info.micro
    label = f"python: {major}.{minor}.{micro}"
    if (major, minor) < (3, 11):
        return (False, f"{_FAIL} {label} (need >= 3.11)")
    return (True, f"{_OK} {label}")


@click.command()
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help=(
        "Exit non-zero on any warning (for CI / image-build gating). "
        "Without --strict, a missing prepared cache is [INFO] only."
    ),
)
@click.option(
    "--quick",
    is_flag=True,
    default=False,
    help=(
        "Run minimal checks only: Python version and prepared-cache presence. "
        "Skips provider, XDG writability, and extended bundle checks."
    ),
)
def doctor(strict: bool, quick: bool) -> None:
    """Run self-diagnostics and report system health."""
    home = Path(os.environ.get("HOME", str(Path.home())))
    cfg = _xdg("XDG_CONFIG_HOME", home / ".config") / "amplifier-agent"
    cache = _xdg("XDG_CACHE_HOME", home / ".cache") / "amplifier-agent"
    state = _xdg("XDG_STATE_HOME", home / ".local" / "state") / "amplifier-agent"

    cache_info = check_cache_state(__version__)
    is_prepared = cache_info.status == "prepared"

    if quick:
        python_ok, python_line = _check_python_version()
        click.echo(python_line)
        cache_prefix = _OK if is_prepared else (_FAIL if strict else _INFO)
        click.echo(f"{cache_prefix} bundle cache: {cache_info.status} ({cache_info.cache_dir})")
        all_ok = python_ok and (is_prepared or not strict)
        if not all_ok:
            sys.exit(1)
        return

    checks: list[tuple[bool, str]] = [
        _check_python_version(),
        _check_provider(),
        _check_writable("config home", cfg),
        _check_writable("cache home", cache),
        _check_writable("state home", state),
    ]

    for _ok, line in checks:
        click.echo(line)

    cache_prefix = _OK if is_prepared else (_FAIL if strict else _INFO)
    click.echo(f"{cache_prefix} bundle cache: {cache_info.status} ({cache_info.cache_dir})")

    hard_failures = not all(ok for ok, _ in checks)
    cache_failure = strict and not is_prepared
    if hard_failures or cache_failure:
        sys.exit(1)
