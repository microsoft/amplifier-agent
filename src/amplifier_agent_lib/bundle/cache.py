"""Bundle cache — cold + warm path: prepare, write to amplifier-agent home cache, and return from cache on hit.

Strategy: pickle (decided in task-2-empirical-spike-pickle).

Cache layout (D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md):
    ~/.amplifier-agent/cache/prepared/<aaa_version>/<sha256(bundle.md)[:16]>/
        prepared.pickle  — pickle.dumps(PreparedBundle)
        manifest.json    — { "aaa_version": "<version>", "bundle_sha256_prefix": "<sha256[:16]>" }

Cache key: (aaa_version, sha256(bundle.md content)[:16]). Editing bundle.md changes the hash and
invalidates the cache automatically. This two-part key fixes the F8 failure mode where two
agents with identical version strings but different manifests would share a cache directory.
Corruption is treated as a cache miss and rebuilt.

Cold path (Task 4): calls load_and_prepare_bundle, writes pickle + manifest.
Warm path (Task 5): if artifact + manifest already exist for this key, deserialise and
return directly without invoking load_and_prepare_bundle.

Note (see ``_restamp_agent_source_paths``): the cache key above says nothing about *where*
``amplifier_agent_lib`` is installed. It is a per-$AMPLIFIER_AGENT_HOME (effectively
per-user) key, not a per-installation one, so it is shared across every installation on the
machine that happens to carry the same version and unmodified vendored ``bundle.md``. The
warm path re-stamps installation-relative absolute paths after deserializing for exactly
this reason.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib import persistence
from amplifier_agent_lib.bundle import AGENTS_DIR, BUNDLE_MD
from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

logger = logging.getLogger(__name__)

_ARTIFACT_NAME: str = "prepared.pickle"
_MANIFEST_NAME: str = "manifest.json"


def _restamp_agent_source_paths(prepared: PreparedBundle) -> None:
    """Re-resolve every agent's ``source_path`` against *this* installation.

    ``prepared.mount_plan["agents"][name]["source_path"]`` is an ABSOLUTE path
    baked in at cold-prepare time by ``bundle/loader.py``
    (``str(bundle.resolve_agent_path(agent_name))``), which resolves under
    whatever ``amplifier_agent_lib`` installation happened to be running at
    that moment (that resolver ultimately derives from ``BUNDLE_DIR =
    Path(__file__).parent``).

    The on-disk cache this value gets pickled into is keyed ONLY by
    ``(aaa_version, sha256(bundle.md content))`` (see
    :func:`cache_dir_for_version`) under ``$AMPLIFIER_AGENT_HOME`` -- a
    per-user location, not a per-installation one. That key says nothing
    about *where* ``amplifier_agent_lib`` is installed, so it is shared by
    every installation on the machine that happens to carry the same version
    and unmodified vendored ``bundle.md`` (a git-pinned dependency
    reinstalled to a new path, a second ``uv tool install`` after the first
    was removed, a dev checkout alongside a packaged install, ...). A
    warm-path hit deserializes whatever absolute paths the FIRST such
    installation baked in, even when the CURRENT installation lives
    somewhere else entirely -- at best a silent path mismatch, at worst a
    ``FileNotFoundError`` reading ``agents/<name>.md`` out of a location that
    no longer exists, surfaced deep inside ``make_turn_handler`` ->
    ``hydrate_agent_overlay`` on the very first turn.

    Fix: after every warm-path deserialize, overwrite each agent's
    ``source_path`` with the equivalent path resolved against *this*
    process's own :data:`~amplifier_agent_lib.bundle.AGENTS_DIR` (itself
    derived from ``Path(__file__)``, so it is always correct for whichever
    installation is actually running -- this is the same fix already applied
    to :data:`~amplifier_agent_lib.resources.BUNDLE_DIR`, extended to the
    per-agent paths derived from it). Mirrors ``bundle/loader.py``'s own
    cold-path rule of only stamping a ``source_path`` that is verified to
    exist; an agent whose file cannot be found here is left exactly as the
    cache provided it (unresolvable either way, so no worse off) rather than
    silently pointing it at an unverified path.

    Mutates ``prepared.mount_plan`` in place. No-op if the mount plan carries
    no ``"agents"`` section (or the pickled object predates this key's use).
    """
    agents = prepared.mount_plan.get("agents") if getattr(prepared, "mount_plan", None) else None
    if not isinstance(agents, dict):
        return
    for name, entry in agents.items():
        if not isinstance(entry, dict) or "source_path" not in entry:
            continue
        candidate = AGENTS_DIR / f"{name}.md"
        if candidate.exists():
            entry["source_path"] = str(candidate)


def cache_dir_for_version(aaa_version: str, bundle_path: Path | None = None) -> Path:
    """Return the cache directory for a specific AaA version and bundle content hash.

    Design reference: D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.

    The cache key is the pair ``(aaa_version, sha256(bundle.md content))``. Using both
    components fixes the F8 failure mode where two agents with identical version strings
    but different bundle manifests would share a cache directory and produce incorrect
    warm-path hits.

    The XDG cache root is owned by :func:`amplifier_agent_lib.persistence.cache_root` —
    this module routes its lookup through there to keep a single source of truth for
    the cache layout (D9 of docs/designs/2026-05-19-baked-in-bundle-decision.md).

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
    return persistence.cache_root() / "prepared" / aaa_version / content_hash


async def load_and_prepare_cached(aaa_version: str) -> PreparedBundle:
    """Load and prepare the vendored bundle, caching the result to XDG cache.

    The cache directory is keyed by ``(aaa_version, sha256(bundle.md content)[:16])``
    (see :func:`cache_dir_for_version`).

    Warm path: if both ``prepared.pickle`` and ``manifest.json`` already exist for this
    key, deserialise and return the cached
    :class:`~amplifier_foundation.bundle._prepared.PreparedBundle` without invoking
    :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`.  A corrupted
    pickle triggers a warning log, removes both stale files, and falls through to the
    cold path. Before returning, every agent's ``source_path`` is re-stamped against
    *this* installation (see :func:`_restamp_agent_source_paths`) — the cache key is
    per-user, not per-installation, so a deserialized path may point at a different
    (possibly since-removed) installation.

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
            prepared = pickle.loads(artifact.read_bytes())
        except Exception as exc:  # broad: corrupt cache → rebuild
            logger.warning(
                "Cache artifact at %s is corrupted (%s); rebuilding.",
                artifact,
                type(exc).__name__,
            )
            artifact.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
        else:
            _restamp_agent_source_paths(prepared)
            return prepared

    # Cold path: prepare from scratch and write to cache.
    prepared = await load_and_prepare_bundle()

    artifact.write_bytes(pickle.dumps(prepared))
    bundle_hash = hashlib.sha256(BUNDLE_MD.read_bytes()).hexdigest()[:16]
    manifest.write_text(json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": bundle_hash}))

    return prepared
