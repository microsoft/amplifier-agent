"""Post-install hook: prime the XDG prepared-bundle cache.

Failures here NEVER fail the install — the runtime first-invocation path is
the safety net.

Entry-point (see pyproject.toml [project.scripts]):
    amplifier-agent-post-install = 'amplifier_agent_lib.post_install:cli_entry'

Usage (in curl/container install scripts):
    uv tool install amplifier-agent && amplifier-agent-post-install
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from amplifier_agent_lib import __version__, persistence
from amplifier_agent_lib.bundle.cache import cache_dir_for_version, load_and_prepare_cached

#: One-time migrations, keyed by a stable id.  A migration runs at most once
#: per machine; the marker file under ``<state_root>/migrations/`` records it.
_CLONE_REFRESH_MIGRATION_ID = "0.14.2-refresh-stale-module-clones"


def _module_clone_root() -> Path:
    """Return the directory holding foundation's git clones of modules.

    Mirrors foundation's own default (``~/.amplifier/cache``).  This is *not*
    :func:`persistence.cache_root`, which is amplifier-agent's own tree --
    module clones belong to foundation and are shared with other Amplifier
    apps on the same machine.
    """
    return Path.home() / ".amplifier" / "cache"


def _refresh_stale_module_clones() -> None:
    """Delete cached module clones once, so the next prepare re-clones them.

    Module sources are declared with a floating ``@main`` ref, but foundation
    resolves a git source by returning the existing clone directory whenever it
    is present and structurally intact -- it never fetches into it.  A clone is
    therefore written exactly once, at first install, and pinned to whatever
    commit ``main`` pointed at that day, for the life of the machine.

    The practical effect is that an upstream fix to a module never reaches an
    existing install.  Reinstalling does not help: the reinstall rebuilds from
    the same frozen clone, restoring the same stale code *and* its stale
    dependency pins.  Deleting the clone is what breaks the cycle, because the
    next prepare has nothing to reuse and clones afresh.

    Scope is deliberately limited to ``amplifier-module-*``.  Bundle and
    foundation clones are frozen by the same mechanism, but widening this
    migration would re-clone roughly a third more repositories to fix a
    problem nobody has reported.

    Never raises: a failure here must not fail an install.  Callers run before
    priming so a wiped clone is immediately re-created.
    """
    marker = persistence.state_root() / "migrations" / f"{_CLONE_REFRESH_MIGRATION_ID}.done"
    if marker.exists():
        return

    removed = 0
    clone_root = _module_clone_root()
    if clone_root.is_dir():
        for child in sorted(clone_root.iterdir()):
            if child.is_dir() and child.name.startswith("amplifier-module-"):
                shutil.rmtree(child, ignore_errors=True)
                removed += 1

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done\n")

    if removed:
        sys.stderr.write(
            f"amplifier-agent: migration {_CLONE_REFRESH_MIGRATION_ID}: removed {removed} stale module clone(s)\n"
        )


async def main() -> int:
    """Prime the prepared-bundle cache for the current version.

    Returns:
        Always 0 — failures are logged to stderr and swallowed so the installer
        never fails due to this hook.
    """
    # Must precede the priming below: the migration only clears the clones,
    # and it is the prepare that follows which re-creates them.
    try:
        _refresh_stale_module_clones()
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"amplifier-agent: module clone refresh skipped ({exc})\n")

    cache_dir = cache_dir_for_version(__version__)
    manifest = cache_dir / "manifest.json"

    # Idempotent: if both exist, the cache is already primed.
    if cache_dir.exists() and manifest.exists():
        sys.stderr.write(f"amplifier-agent: cache already prepared at {cache_dir}\n")
        return 0

    try:
        await load_and_prepare_cached(aaa_version=__version__)
        sys.stderr.write(f"amplifier-agent: prepared bundle cached at {cache_dir}\n")
    except Exception as exc:
        sys.stderr.write(
            f"amplifier-agent: post-install cache prime failed ({exc}); first invocation will prepare instead.\n"
        )

    return 0


def cli_entry() -> None:
    """Entry-point wrapper for amplifier-agent-post-install script."""
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli_entry()
