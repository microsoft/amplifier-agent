"""Post-install hook: refresh floating module clones, then prime the cache.

Failures here NEVER fail the install -- the runtime first-invocation path is
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

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import cache_dir_for_version, load_and_prepare_cached
from amplifier_agent_lib.foundation_home import module_cache_root

#: Prefix identifying a module clone inside foundation's cache directory.
#:
#: Foundation names each clone ``<repo-name>-<sha256(url@ref)[:16]>``, so this
#: matches ``amplifier-module-*`` clones and nothing else in that directory.
_MODULE_CLONE_PREFIX = "amplifier-module-"


def _refresh_floating_module_clones() -> int:
    """Delete cached module clones so the prepare that follows re-clones them.

    Why this is necessary
    ---------------------
    Every module source in ``bundle.md`` is declared at a floating ``@main``
    ref, deliberately: pinning would gate amplifier-agent releases on module-repo
    state (``docs/spec/bundle-and-cache.md``, non-goals).  But foundation's git
    source handler returns an existing clone directory whenever it is present
    and structurally intact -- it never fetches into it, never compares refs,
    never checks the commit.  The clone path is keyed on
    ``sha256(git_url@ref)[:16]``, so a floating-ref clone owns one stable
    directory written exactly once, at first install, and pinned to whatever
    ``main`` pointed at that day for the life of the machine.

    Reinstalling does not help: ``uv tool install --reinstall --force`` empties
    the tool venv and genuinely reinstalls every module, but each one rebuilds
    *from the same frozen clone*, restoring the same stale code and its stale
    dependency pins.  Deleting the clone is the only lever that works.

    Why it is safe to delete
    ------------------------
    Because of the decoupling this hook now depends on: the clone root is
    :func:`~amplifier_agent_lib.foundation_home.module_cache_root`, inside
    amplifier-agent's own tree.  Before that change these clones lived in
    ``~/.amplifier/cache``, shared with amplifier-app-cli, where deleting them
    would have been a cross-application side effect on a directory this
    application does not own.

    Why it runs unconditionally rather than behind a one-shot marker
    ---------------------------------------------------------------
    The caller only reaches this function on a *cold* cache -- see :func:`main`.
    A cold cache means either a fresh install (nothing to delete; this is a
    no-op) or a version change (exactly when stale clones must be refreshed).
    That makes the version-keyed prepared-bundle cache the idempotence
    mechanism, so no separate migration marker is needed and the refresh cannot
    silently stop working the way a one-shot marker eventually does.

    Returns:
        Number of clone directories removed.  Never raises: the caller treats
        a failure here as non-fatal, and a failed delete degrades to the old
        stale-clone behaviour rather than a broken install.
    """
    removed = 0
    clone_root = module_cache_root()
    if not clone_root.is_dir():
        return 0

    for child in sorted(clone_root.iterdir()):
        if child.is_dir() and child.name.startswith(_MODULE_CLONE_PREFIX):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1

    return removed


async def main() -> int:
    """Prime the prepared-bundle cache for the current version.

    Returns:
        Always 0 -- failures are logged to stderr and swallowed so the installer
        never fails due to this hook.
    """
    cache_dir = cache_dir_for_version(__version__)
    manifest = cache_dir / "manifest.json"

    # Idempotent: if both exist, the cache is already primed.  Returning here
    # is also what keeps the clone refresh below from running on every
    # invocation -- see _refresh_floating_module_clones' docstring.
    if cache_dir.exists() and manifest.exists():
        sys.stderr.write(f"amplifier-agent: cache already prepared at {cache_dir}\n")
        return 0

    # Ordered before priming deliberately: this only *removes* clones; the
    # prepare that follows is what re-creates them.  Wrapped so a failure here
    # can never block an install.
    try:
        removed = _refresh_floating_module_clones()
        if removed:
            sys.stderr.write(f"amplifier-agent: refreshed {removed} stale module clone(s)\n")
    except Exception as exc:
        sys.stderr.write(f"amplifier-agent: module clone refresh skipped ({exc})\n")

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
