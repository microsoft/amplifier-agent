"""Bundle cache — cold + warm path: prepare, write to XDG cache, and return from cache on hit.

Strategy: pickle (decided in task-2-empirical-spike-pickle).

Cache layout (D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md):
    $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)[:16]>/
        prepared.pickle  — pickle.dumps(PreparedBundle)
        manifest.json    — { "aaa_version": "<version>", "bundle_sha256_prefix": "<sha256[:16]>" }

Cache key: (aaa_version, sha256(bundle.md content)). Bumping AaA version or modifying bundle.md
invalidates the cache automatically. This two-part key fixes the F8 failure mode where two
agents with identical version strings but different manifests would share a cache directory.
Corruption is treated as a cache miss and rebuilt.

Cold path (Task 4): calls load_and_prepare_bundle, writes pickle + manifest.
Warm path (Task 5): if artifact + manifest already exist for this key, deserialise and
return directly without invoking load_and_prepare_bundle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle import BUNDLE_MD
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


def cache_dir_for_version(aaa_version: str, bundle_path: Path | None = None) -> Path:
    """Return the cache directory for a specific AaA version and bundle content hash.

    Design reference: D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.

    The cache key is the pair ``(aaa_version, sha256(bundle.md content))``. Using both
    components fixes the F8 failure mode where two agents with identical version strings
    but different bundle manifests would share a cache directory and produce incorrect
    warm-path hits.

    Args:
        aaa_version: The AaA package version string (e.g. ``"1.0.0"``).
        bundle_path: Path to the bundle manifest file whose content contributes to the
            cache key.  Defaults to the vendored :data:`~amplifier_agent_lib.bundle.BUNDLE_MD`
            when ``None``.

    Returns:
        A :class:`~pathlib.Path` to the ``<aaa_version>/<sha256[:16]>`` cache directory.
        The directory may not yet exist; callers are responsible for creating it.
    """
    target = bundle_path if bundle_path is not None else BUNDLE_MD
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
    return _xdg_cache_home() / _CACHE_SUBDIR / aaa_version / content_hash


async def load_and_prepare_cached(aaa_version: str) -> PreparedBundle:
    """Load and prepare the vendored bundle, caching the result to XDG cache.

    The cache directory is keyed by ``(aaa_version, sha256(bundle.md content)[:16])``
    (see :func:`cache_dir_for_version`).

    Warm path: if both ``prepared.pickle`` and ``manifest.json`` already exist for this
    key, deserialise and return the cached
    :class:`~amplifier_foundation.bundle._prepared.PreparedBundle` without invoking
    :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`.  A corrupted
    pickle triggers a warning log, removes both stale files, and falls through to the
    cold path.

    Cold path: calls
    :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`, writes the
    resulting PreparedBundle to the version+hash-keyed cache directory as a pickled
    blob alongside a ``manifest.json`` recording ``{ "aaa_version", "bundle_sha256_prefix" }``.

    Args:
        aaa_version: The AaA package version string used as part of the cache key.

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
    bundle_hash = hashlib.sha256(BUNDLE_MD.read_bytes()).hexdigest()[:16]
    manifest.write_text(json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": bundle_hash}))

    return prepared
