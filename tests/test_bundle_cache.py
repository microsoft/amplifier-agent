"""Tests for bundle/cache.py — cold path: first invocation prepare + write to XDG cache.

Strategy: pickle (decided in task-2-empirical-spike-pickle).
Cache layout: $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/
    prepared.pickle  — pickled PreparedBundle
    manifest.json    — { aaa_version }
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_cold_invocation_creates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First call to load_and_prepare_cached() creates the version-keyed cache dir with artifacts."""
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version, load_and_prepare_cached

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    cache_root = cache_dir_for_version("1.0.0")
    assert not cache_root.exists(), "Cache dir should not exist before first call"

    prepared = await load_and_prepare_cached(aaa_version="1.0.0")

    assert prepared is not None
    assert cache_root.exists(), "Cache dir should be created after first call"
    files_in_cache = list(cache_root.iterdir())
    assert len(files_in_cache) >= 1, "At least one artifact file should be in cache dir"


def test_cache_dir_is_xdg_keyed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cache_dir_for_version() uses XDG_CACHE_HOME and keys by (version, bundle content hash).

    Cache layout after D2 design change: <XDG_CACHE_HOME>/amplifier-agent/prepared/<version>/<hash>/
    Different versions use different intermediate directories (version segment differs).
    Both share the same grandparent (the prepared/ directory).
    """
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    v1 = cache_dir_for_version("1.0.0")
    v2 = cache_dir_for_version("2.0.0")

    # Different versions must produce different paths.
    assert v1 != v2, "Different versions must produce different cache dirs"
    # The version string appears as an intermediate directory segment.
    assert "1.0.0" in str(v1), "Version string should be in the v1 path"
    assert "2.0.0" in str(v2), "Version string should be in the v2 path"
    # Both paths respect XDG_CACHE_HOME and include the service subdirectory.
    assert str(tmp_path) in str(v1), "XDG_CACHE_HOME should be in the path"
    assert "amplifier-agent" in str(v1), "'amplifier-agent' should be in the path"
    # Both share the same grandparent (the prepared/ directory under XDG_CACHE_HOME).
    assert v1.parent.parent == v2.parent.parent, "Both versions share the same prepared/ ancestor"


@pytest.mark.asyncio
async def test_warm_invocation_hits_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call returns the cached PreparedBundle without invoking load_and_prepare_bundle."""
    import amplifier_agent_lib.bundle.cache as cache_mod
    from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Cold call — writes the pickle + manifest to the cache directory.
    await load_and_prepare_cached(aaa_version="1.0.0")

    # Install a sentinel loader: if the warm path incorrectly falls through
    # to the cold path it will call this and raise RuntimeError.
    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("loader should not be called on warm path")

    monkeypatch.setattr(cache_mod, "load_and_prepare_bundle", boom)

    # Warm call — must return successfully without invoking boom.
    result = await load_and_prepare_cached(aaa_version="1.0.0")
    assert result is not None


@pytest.mark.asyncio
async def test_new_version_invalidates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bumping aaa_version writes to a new dir; old dir is untouched (downgrade safe)."""
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version, load_and_prepare_cached

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Cold call for version 1.0.0 — writes to v1 cache dir.
    await load_and_prepare_cached(aaa_version="1.0.0")
    v1_dir = cache_dir_for_version("1.0.0")
    assert v1_dir.exists(), "v1 cache dir should exist after first call"

    # Cold call for version 2.0.0 — writes to a NEW dir, does not touch v1.
    await load_and_prepare_cached(aaa_version="2.0.0")
    v2_dir = cache_dir_for_version("2.0.0")
    assert v2_dir.exists(), "v2 cache dir should exist after second call"
    assert v1_dir != v2_dir, "different versions must use different cache dirs"
    assert v1_dir.exists(), "old version cache must NOT be deleted (downgrade safe)"


@pytest.mark.asyncio
async def test_corrupted_cache_triggers_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupted cache artifact is treated as a miss; cache is rebuilt with a warning; no raise."""
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version, load_and_prepare_cached

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Prime the cache via a cold call.
    prepared_first = await load_and_prepare_cached(aaa_version="1.0.0")
    assert prepared_first is not None

    cache_root = cache_dir_for_version("1.0.0")

    # Find artifacts (excluding manifest.json) and assert at least one exists.
    artifacts = [f for f in cache_root.iterdir() if f.name != "manifest.json"]
    assert len(artifacts) >= 1, "At least one non-manifest artifact should exist in cache"

    # Corrupt the artifact(s).
    for artifact in artifacts:
        artifact.write_bytes(b"not-a-valid-cache-artifact-\x00\xff")

    # Re-call with corruption recovery — must NOT raise and must emit a warning.
    with caplog.at_level(logging.WARNING, logger="amplifier_agent_lib.bundle.cache"):
        prepared = await load_and_prepare_cached(aaa_version="1.0.0")

    assert prepared is not None, "load_and_prepare_cached must return a PreparedBundle after rebuilding"
    assert any("cache" in record.message.lower() for record in caplog.records), (
        "Expected a warning log containing 'cache' from the cache logger"
    )


@pytest.mark.asyncio
async def test_cache_dir_includes_bundle_content_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Different bundle.md content must produce different cache dirs under the same version.

    Design reference: D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.
    The cache key must incorporate sha256(bundle.md) so that two agents with identical
    version strings but different manifests never share a cache directory.
    """
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    bundle_a = tmp_path / "a.md"
    bundle_a.write_text("---\nbundle:\n  name: a\n  version: 1.0.0\n---\n")

    bundle_b = tmp_path / "b.md"
    bundle_b.write_text("---\nbundle:\n  name: b\n  version: 1.0.0\n---\n")

    dir_a = cache_dir_for_version("1.0.0", bundle_path=bundle_a)
    dir_b = cache_dir_for_version("1.0.0", bundle_path=bundle_b)

    assert dir_a != dir_b, "Different bundle content must produce different cache dirs"
    assert dir_a.parent == dir_b.parent, "Different bundle content must share the same version parent"


@pytest.mark.asyncio
async def test_cache_dir_stable_for_same_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling cache_dir_for_version twice with the same bundle produces the same dir."""
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    bundle = tmp_path / "b.md"
    bundle.write_text("---\nbundle:\n  name: stable\n  version: 1.0.0\n---\n")

    first = cache_dir_for_version("1.0.0", bundle_path=bundle)
    second = cache_dir_for_version("1.0.0", bundle_path=bundle)

    assert first == second


@pytest.mark.asyncio
async def test_manifest_edit_invalidates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any edit to the bundle manifest must change the cache key.

    Regression guard for the F8 failure mode: two bundle versions sharing a cache dir
    because the key was derived only from aaa_version and not from bundle content.
    See docs/designs/2026-05-19-baked-in-bundle-decision.md.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    dir_before = cache_dir_for_version("1.0.0", bundle_path=BUNDLE_MD)

    edited = tmp_path / "edited.md"
    edited.write_text(BUNDLE_MD.read_text() + "\n# trivial edit\n")

    dir_after = cache_dir_for_version("1.0.0", bundle_path=edited)

    assert dir_before != dir_after, "Manifest edit must change the cache key"
