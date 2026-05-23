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

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import yaml as _yaml

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


def _emit_bundle_shas() -> None:
    """Emit sha256-of-source-URL lines for every module declared in bundle.md.

    v1 stub: SHA is computed over the ``source:`` URL string, not over the
    installed module's content. This still detects supply-chain drift at the
    *manifest* level — if bundle.md is edited (URL changed, pin added/removed),
    a baseline diff will fire. Full content-pinning is tracked as D-v1.x-02.

    Output format (one line per module, sorted by module name):
        sha256_prefix=<16-hex>  module=<name>  source=<url>

    Errors (missing bundle, malformed YAML) are reported as ``[FAIL]`` lines on
    stderr; this function does not raise.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD

    try:
        text = BUNDLE_MD.read_text("utf-8")
    except FileNotFoundError as exc:
        click.echo(f"{_FAIL} emit-sha: bundle.md not found ({exc})", err=True)
        return

    parts = text.split("---\n")
    if len(parts) < 3:
        click.echo(
            f"{_FAIL} emit-sha: bundle.md has no YAML frontmatter "
            f"(expected at least 3 '---'-delimited parts, got {len(parts)})",
            err=True,
        )
        return

    try:
        manifest = _yaml.safe_load(parts[1])
    except _yaml.YAMLError as exc:
        click.echo(f"{_FAIL} emit-sha: bundle.md YAML parse error: {exc}", err=True)
        return

    if not isinstance(manifest, dict):
        click.echo(
            f"{_FAIL} emit-sha: bundle.md frontmatter is not a mapping (got {type(manifest).__name__})",
            err=True,
        )
        return

    session = manifest.get("session", {}) or {}
    entries: list[tuple[str, str]] = []

    for slot in ("orchestrator", "context", "provider"):
        block = session.get(slot)
        if isinstance(block, dict):
            name = block.get("module")
            src = block.get("source")
            if isinstance(name, str) and isinstance(src, str):
                entries.append((name, src))

    for collection_key in ("tools", "hooks"):
        items = manifest.get(collection_key) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("module")
            src = item.get("source")
            if isinstance(name, str) and isinstance(src, str):
                entries.append((name, src))

    click.echo("# bundle module source SHAs (v1: sha of source URL string)")
    for name, src in sorted(entries, key=lambda pair: pair[0]):
        sha_prefix = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        click.echo(f"sha256_prefix={sha_prefix}  module={name}  source={src}")


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
@click.option(
    "--emit-sha",
    is_flag=True,
    default=False,
    help=(
        "Emit sha256 of each bundle module source URL for supply-chain "
        "baseline diffing. v1 stub: SHA is of the source URL string. "
        "Full content SHA is D-v1.x-02."
    ),
)
def doctor(strict: bool, quick: bool, emit_sha: bool) -> None:
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

    if emit_sha:
        _emit_bundle_shas()

    hard_failures = not all(ok for ok, _ in checks)
    cache_failure = strict and not is_prepared
    if hard_failures or cache_failure:
        sys.exit(1)
