"""Tests for admin/cache_clear.py — wiring cache clear to remove prepared cache."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_clear_cache_removes_prepared_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """clear_cache() removes the prepared cache root directory and returns ClearResult(existed=True)."""
    from amplifier_agent_cli.admin.cache_clear import clear_cache
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    # Create a versioned cache directory with artifacts (simulating a real cache)
    v = cache_dir_for_version("1.0.0")
    v.mkdir(parents=True)
    (v / "prepared.pickle").write_bytes(b"fake-pickle-data")
    (v / "manifest.json").write_text('{"aaa_version": "1.0.0"}')

    result = clear_cache()

    assert result.existed is True
    assert not v.exists(), "Version cache dir should be removed"


def test_clear_cache_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling clear_cache() twice must not raise, even when no cache exists on second call."""
    from amplifier_agent_cli.admin.cache_clear import clear_cache

    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    # First call — no cache present
    clear_cache()
    # Second call — still no cache, must not raise
    clear_cache()
