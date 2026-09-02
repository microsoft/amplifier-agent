"""Bundle cache — cold + warm path: prepare, write to amplifier-agent home cache, and return from cache on hit.

Strategy: pickle (decided in task-2-empirical-spike-pickle).

Cache layout (D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md):
    ~/.amplifier-agent/cache/prepared/<aaa_version>/<sha256(bundle.md)[:16]>/
        prepared.pickle  — pickle.dumps(PreparedBundle)
        manifest.json    — { "aaa_version": "<version>", "bundle_sha256_prefix": "<sha256[:16]>" }

Cache key: (aaa_version, sha256(bundle.md content)). Bumping AaA version or modifying bundle.md
invalidates the cache automatically. This two-part key fixes the F8 failure mode where two
agents with identical version strings but different manifests would share a cache directory.
Corruption is treated as a cache miss and rebuilt.

Cold path (Task 4): calls load_and_prepare_bundle, writes pickle + manifest.
Warm path (Task 5): if artifact + manifest already exist for this key, deserialise and
return directly without invoking load_and_prepare_bundle.

STALE ABSOLUTE PATHS (installation-relocation defect, cortex-90ei): the cache key says
nothing about *where on disk* this particular installation's files live -- only its
version and its bundle content hash. ``load_and_prepare_bundle`` resolves each declared
agent to an absolute ``source_path`` (see ``loader.py``) computed relative to wherever
``amplifier_agent_lib`` happens to be imported from *right now*, and that absolute string
is what gets pickled into the artifact. ``~/.amplifier-agent/cache/`` is a single
machine-global directory: any process with a matching (version, hash) shares it, even if
it is a completely different installation -- a different venv, or the very same venv
rebuilt onto a different Python minor version (e.g. reinstalling a uv tool moves every
``.../lib/python3.12/site-packages/...`` path to ``.../lib/python3.13/site-packages/...``).
A warm hit that predates such a rebuild still matches the key, so without this check it
would be returned as-is with absolute agent paths pointing at a ``lib/pythonX.Y``
directory that may no longer exist under this process's own installation.

Rather than enumerate every way an installation can move (or hardcode which Python
version is "right"), the warm path re-verifies -- on every hit -- that each agent's
cached ``source_path`` still exists as seen by *this* process. A miss is treated exactly
like corruption: logged, the stale files removed, and control falls through to the cold
path, which re-resolves every path fresh against whatever is actually running right now.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib import persistence
from amplifier_agent_lib.bundle import BUNDLE_MD
from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

logger = logging.getLogger(__name__)

_ARTIFACT_NAME: str = "prepared.pickle"
_MANIFEST_NAME: str = "manifest.json"


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


def _stale_agent_source_paths(prepared: PreparedBundle) -> list[str]:
    """Names of agents whose cached ``source_path`` no longer exists on disk.

    ``loader.py`` only ever sets ``source_path`` when the file was actually found at
    prepare time (see :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`),
    so an agent with no ``source_path`` at all was never resolvable and is not this
    function's concern -- only an agent that WAS resolvable once, but whose recorded
    absolute path no longer resolves *for this process*, counts as stale. That is the
    exact shape of the installation-relocation defect this guards against (cortex-90ei):
    a machine-global cache entry, written by one installation (e.g. one Python minor
    version's site-packages), being warm-hit by a different installation (e.g. the same
    venv rebuilt onto a different Python minor version) whose files live somewhere else.
    """
    stale: list[str] = []
    for agent_name, agent_def in prepared.bundle.agents.items():
        source_path = agent_def.get("source_path")
        if source_path and not Path(source_path).exists():
            stale.append(agent_name)
    return stale


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

    # Warm path: both files exist — return the cached PreparedBundle directly, but
    # only once we've confirmed its baked-in absolute agent paths still resolve for
    # THIS process (see the module docstring's "STALE ABSOLUTE PATHS" section).
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
            stale = _stale_agent_source_paths(prepared)
            if not stale:
                return prepared
            logger.warning(
                "Cache artifact at %s has stale agent source_path(s) for %s -- this "
                "installation's files live somewhere the cached absolute paths no "
                "longer point to (e.g. the venv was rebuilt onto a different Python "
                "minor version since this entry was written); rebuilding.",
                artifact,
                ", ".join(sorted(stale)),
            )
            artifact.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)

    # Cold path: prepare from scratch and write to cache.
    prepared = await load_and_prepare_bundle()

    artifact.write_bytes(pickle.dumps(prepared))
    bundle_hash = hashlib.sha256(BUNDLE_MD.read_bytes()).hexdigest()[:16]
    manifest.write_text(json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": bundle_hash}))

    return prepared
