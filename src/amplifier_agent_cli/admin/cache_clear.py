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


def clear_legacy_module_clones() -> tuple[int, Path]:
    """Remove module clones stranded in amplifier-app-cli's tree.

    Opt-in only, never called implicitly.  Before the foundation-cache
    relocation, amplifier-agent's module clones were written into
    ``~/.amplifier/cache``; afterwards nothing this application runs consults
    them, but they are not removed automatically because that directory is
    foundation's default for every Amplifier application on the machine, and
    clone directories are keyed by ``sha256(git_url@ref)[:16]`` with no
    per-application namespacing.  On a machine that also runs amplifier-app-cli
    these are its live clones.

    PR #141 removed them unconditionally, which was correct while the directory
    was the one amplifier-agent itself used.  It is not correct now, so the
    removal is offered rather than performed.

    Returns:
        ``(count_removed, legacy_root)``.  Count is 0 when nothing was found.
    """
    from amplifier_agent_lib.foundation_home import find_legacy_module_clones, legacy_module_clone_root

    removed = 0
    for clone in find_legacy_module_clones():
        shutil.rmtree(clone, ignore_errors=True)
        removed += 1
    return removed, legacy_module_clone_root()


def main(*, legacy: bool = False) -> int:
    """Print result of cache clear to stderr and return exit code 0.

    Args:
        legacy: Also remove module clones stranded in amplifier-app-cli's tree.

    Returns:
        0 always (idempotent operation).
    """
    result = clear_cache()
    if result.existed:
        print(f"Removed cache at {result.removed_path}", file=sys.stderr)
    else:
        print(f"No cache present at {result.removed_path}", file=sys.stderr)

    if legacy:
        count, root = clear_legacy_module_clones()
        if count:
            print(f"Removed {count} legacy module clone(s) from {root}", file=sys.stderr)
        else:
            print(f"No legacy module clones present in {root}", file=sys.stderr)
    return 0


def run() -> int:
    """Thin wrapper — legacy entry-point alias for main()."""
    return main()


@click.group()
def cache_group() -> None:
    """Manage the prepared-bundle cache."""


@cache_group.command(name="clear")
@click.option(
    "--legacy",
    is_flag=True,
    default=False,
    help=(
        "Also remove module clones stranded in ~/.amplifier/cache by versions "
        "before 0.14.2. amplifier-agent no longer uses them, but that directory "
        "is shared with amplifier-app-cli — only pass this if you do not run it."
    ),
)
def cache_clear(legacy: bool) -> None:
    """Remove the prepared-bundle cache (idempotent)."""
    sys.exit(main(legacy=legacy))
