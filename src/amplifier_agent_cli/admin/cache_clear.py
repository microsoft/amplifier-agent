"""Admin commands: cache subgroup with the 'clear' command.

Removes the entire prepared-bundle cache root at
$XDG_CACHE_HOME/amplifier-agent/prepared/ (all version subdirectories).

Uses cache_dir_for_version('_').parent.parent to derive the root, which resolves to
$XDG_CACHE_HOME/amplifier-agent/prepared/ — the directory that holds all
per-version cache subdirectories. The extra .parent is required because
cache_dir_for_version now returns a two-level path: <version>/<content_hash>/
(D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md).
Clearing the root removes every cached version and every bundle hash under it.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from amplifier_agent_lib.bundle.cache import cache_dir_for_version


@dataclass
class ClearResult:
    """Result of a cache clear operation."""

    removed_path: Path
    existed: bool


def clear_cache() -> ClearResult:
    """Remove the XDG prepared-bundle cache root (idempotent).

    Derives the root as cache_dir_for_version('_').parent.parent, which resolves to
    $XDG_CACHE_HOME/amplifier-agent/prepared/ — the ancestor of all version and
    content-hash subdirectories. The extra .parent is required because
    cache_dir_for_version returns a two-level path: <version>/<content_hash>/
    after the D2 design change. Repeated calls do not error when the directory is absent.

    Returns:
        A :class:`ClearResult` with the path that was (or would have been)
        removed and whether it existed before removal.
    """
    root = cache_dir_for_version("_").parent.parent
    assert root.name == "prepared", (
        f"cache root has unexpected name {root.name!r}; expected 'prepared'. "
        "cache_dir_for_version() layout may have changed — audit clear_cache()."
    )
    existed = root.exists()
    if existed:
        shutil.rmtree(root)
    return ClearResult(removed_path=root, existed=existed)


def main() -> int:
    """Print result of cache clear to stderr and return exit code 0.

    Returns:
        0 always (idempotent operation).
    """
    result = clear_cache()
    if result.existed:
        print(f"Removed cache at {result.removed_path}", file=sys.stderr)
    else:
        print(f"No cache present at {result.removed_path}", file=sys.stderr)
    return 0


def run() -> int:
    """Thin wrapper — legacy entry-point alias for main()."""
    return main()


@click.group()
def cache_group() -> None:
    """Manage the prepared-bundle cache."""


@cache_group.command(name="clear")
def cache_clear() -> None:
    """Remove the prepared-bundle cache (idempotent)."""
    sys.exit(main())
