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
import sys

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import cache_dir_for_version, load_and_prepare_cached


async def main() -> int:
    """Prime the prepared-bundle cache for the current version.

    Returns:
        Always 0 — failures are logged to stderr and swallowed so the installer
        never fails due to this hook.
    """
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
