"""Built-in (sealed) bundle for amplifier-agent.

This package vendors the built-in bundle definition used by the amplifier-agent CLI.
The bundle is sealed (per D4 design decision) — it declares the orchestrator, context
modules, and foundation bundle that constitute the standard agent environment.

Usage (internal):
    The bundle is loaded via ``amplifier_foundation.load_bundle(BUNDLE_MD)`` and the
    prepared result is cached to
    ``$XDG_CACHE_HOME/amplifier-agent/prepared/<version>/``
    so that repeated process starts do not re-resolve the bundle from scratch.

IMPORTANT: Do not edit bundle.md outside of a deliberate design change. Editing the
    bundle changes the cache key and invalidates all cached sessions.
"""

from pathlib import Path

#: Directory containing this package (and the vendored bundle.md).
BUNDLE_DIR: Path = Path(__file__).parent

#: Absolute path to the vendored bundle.md shipped inside this package.
BUNDLE_MD: Path = BUNDLE_DIR / "bundle.md"
