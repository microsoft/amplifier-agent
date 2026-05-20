"""Bundle cache — cold + warm path: prepare, write to XDG cache, and return from cache on hit.

Strategy: pickle (decided in task-2-empirical-spike-pickle).

Cache layout:
    $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/
        prepared.pickle  — pickle.dumps(PreparedBundle)
        manifest.json    — { "aaa_version": "<version>" }

Cache key: aaa_version string (AaA package version). Bumping AaA invalidates
the cache automatically. Corruption is treated as a cache miss and rebuilt
(handled in Task 7).

Cold path (Task 4): calls load_and_prepare_bundle, writes pickle + manifest.
Warm path (Task 5): if artifact + manifest already exist, deserialise and
return directly without invoking load_and_prepare_bundle.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

logger = logging.getLogger(__name__)

_CACHE_SUBDIR: str = "amplifier-agent/prepared"
_ARTIFACT_NAME: str = "prepared.pickle"
_MANIFEST_NAME: str = "manifest.json"


def _xdg_cache_home() -> Path:
    """Return the XDG cache home directory.

    Reads ``$XDG_CACHE_HOME`` if set; falls back to ``~/.cache``.
    """
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".cache"


def cache_dir_for_version(aaa_version: str) -> Path:
    """Return the cache directory for a specific AaA version.

    Args:
        aaa_version: The AaA package version string (e.g. ``"1.0.0"``).

    Returns:
        A :class:`~pathlib.Path` to the version-keyed cache directory.
        The directory may not yet exist; callers are responsible for creating it.
    """
    return _xdg_cache_home() / _CACHE_SUBDIR / aaa_version


async def load_and_prepare_cached(aaa_version: str) -> PreparedBundle:
    """Load and prepare the vendored bundle, caching the result to XDG cache.

    Warm path (Task 5): if both ``prepared.pickle`` and ``manifest.json``
    already exist for this version, deserialise and return the cached
    :class:`~amplifier_foundation.bundle._prepared.PreparedBundle` without
    invoking :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`.

    Cold path (Task 4): calls
    :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`, writes
    the resulting PreparedBundle to the version-keyed cache directory as a
    pickled blob alongside a ``manifest.json`` describing the cache entry.

    Args:
        aaa_version: The AaA package version string used as the cache key.

    Returns:
        A :class:`~amplifier_foundation.bundle._prepared.PreparedBundle`
        ready for session creation.
    """
    cache_dir = cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)

    artifact = cache_dir / _ARTIFACT_NAME
    manifest = cache_dir / _MANIFEST_NAME

    # Warm path: both files exist — return the cached PreparedBundle directly.
    if artifact.exists() and manifest.exists():
        try:
            return pickle.loads(artifact.read_bytes())
        except Exception as exc:  # broad: corrupt cache → rebuild
            logger.warning(
                "Cache artifact at %s is corrupted (%s); rebuilding.",
                artifact,
                type(exc).__name__,
            )
            artifact.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)

    # Cold path: prepare from scratch and write to cache.
    prepared = await load_and_prepare_bundle()

    artifact.write_bytes(pickle.dumps(prepared))
    manifest.write_text(json.dumps({"aaa_version": aaa_version}))

    return prepared
