"""Tests for the XDG-to-~/.amplifier-agent/ one-shot migration.

Design: docs/designs/2026-06-11-drop-xdg-and-flag-cleanup.md (Phase 1.4).

All tests use AMPLIFIER_AGENT_HOME and XDG_STATE_HOME / XDG_CACHE_HOME /
XDG_CONFIG_HOME monkeypatches to construct legacy and target layouts inside
tmp_path without touching the real home directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_agent_lib import migration
from amplifier_agent_lib.migration import maybe_migrate_legacy_xdg_storage
from amplifier_agent_lib.persistence import amplifier_agent_home

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Create legacy XDG directories with some content under tmp_path.

    Sets XDG_*_HOME to point inside tmp_path so _legacy_state/cache/config()
    resolve there, and sets AMPLIFIER_AGENT_HOME to a sibling 'aah' directory
    so amplifier_agent_home() resolves to a clean target.

    Returns a dict with 'state', 'cache', 'config' (legacy paths) and 'aah'.
    """
    legacy_state = tmp_path / "xdg_state" / "amplifier-agent"
    legacy_cache = tmp_path / "xdg_cache" / "amplifier-agent"
    legacy_config = tmp_path / "xdg_config" / "amplifier-agent"
    aah = tmp_path / "aah"

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(aah))

    # Create legacy state directory with a transcript.
    legacy_state.mkdir(parents=True, exist_ok=True)
    (legacy_state / "sessions").mkdir()
    (legacy_state / "sessions" / "old-sid").mkdir()
    (legacy_state / "sessions" / "old-sid" / "transcript.jsonl").write_text(
        '{"role":"user","content":"hi"}',
        encoding="utf-8",
    )

    # Create legacy cache directory.
    legacy_cache.mkdir(parents=True, exist_ok=True)
    (legacy_cache / "prepared").mkdir()

    # Create legacy config directory.
    legacy_config.mkdir(parents=True, exist_ok=True)
    (legacy_config / "host_config.json").write_text('{"approval":{"auto_approve":true}}', encoding="utf-8")

    return {"state": legacy_state, "cache": legacy_cache, "config": legacy_config, "aah": aah}


# ---------------------------------------------------------------------------
# 1. Sentinel present → no-op
# ---------------------------------------------------------------------------


def test_sentinel_present_returns_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the sentinel file already exists, the migration is a no-op."""
    aah = tmp_path / "aah"
    aah.mkdir()
    sentinel = aah / ".migrated_from_xdg"
    sentinel.write_text("already done", encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(aah))

    result = maybe_migrate_legacy_xdg_storage()

    assert result.skipped is True
    assert result.migrated == 0
    assert result.from_xdg is True
    # Sentinel untouched.
    assert sentinel.read_text(encoding="utf-8") == "already done"


# ---------------------------------------------------------------------------
# 2. Legacy state present, target absent → migration runs, sentinel written
# ---------------------------------------------------------------------------


def test_migration_moves_all_three_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When all three legacy XDG dirs exist and targets are absent, all are moved."""
    dirs = _seed_legacy(tmp_path, monkeypatch)
    aah = dirs["aah"]

    result = maybe_migrate_legacy_xdg_storage()

    assert result.migrated == 3
    assert result.skipped is False
    assert result.collided == 0
    assert result.from_xdg is True

    # State moved.
    transcript = aah / "state" / "sessions" / "old-sid" / "transcript.jsonl"
    assert transcript.is_file(), f"transcript not found at {transcript}"
    assert transcript.read_text(encoding="utf-8") == '{"role":"user","content":"hi"}'

    # Cache moved.
    assert (aah / "cache" / "prepared").is_dir()

    # Config moved.
    host_cfg = aah / "config" / "host_config.json"
    assert host_cfg.is_file()

    # Sentinel written.
    sentinel = aah / ".migrated_from_xdg"
    assert sentinel.exists()
    sentinel_text = sentinel.read_text(encoding="utf-8")
    assert "migrated at" in sentinel_text


def test_migration_partial_legacy_only_state_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When only the legacy state dir exists, only it is moved; no error on missing others."""
    aah = tmp_path / "aah"
    legacy_state = tmp_path / "xdg_state" / "amplifier-agent"
    legacy_state.mkdir(parents=True)
    (legacy_state / "marker.txt").write_text("state-data", encoding="utf-8")

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(aah))

    result = maybe_migrate_legacy_xdg_storage()

    assert result.migrated == 1
    assert result.skipped is False
    assert result.collided == 0
    assert (aah / "state" / "marker.txt").read_text(encoding="utf-8") == "state-data"
    assert (aah / ".migrated_from_xdg").exists()


# ---------------------------------------------------------------------------
# 3. Legacy + target both present (collision) → skip, no destruction
# ---------------------------------------------------------------------------


def test_collision_leaves_legacy_in_place(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If a target subdir already exists, the legacy dir is left intact (I6)."""
    dirs = _seed_legacy(tmp_path, monkeypatch)
    aah = dirs["aah"]

    # Pre-create the state target so a collision occurs.
    (aah / "state").mkdir(parents=True)
    (aah / "state" / "existing.txt").write_text("new-data", encoding="utf-8")

    result = maybe_migrate_legacy_xdg_storage()

    # State collided; cache and config migrated successfully.
    assert result.collided == 1
    assert result.migrated == 2
    assert result.from_xdg is True

    # Legacy state is untouched (no data destruction, I6).
    legacy_state = dirs["state"]
    assert legacy_state.is_dir(), "legacy state must not be removed on collision"
    assert (legacy_state / "sessions" / "old-sid" / "transcript.jsonl").is_file()

    # Target state is also intact (the new data was not clobbered).
    assert (aah / "state" / "existing.txt").read_text(encoding="utf-8") == "new-data"

    # Sentinel still written (collision is not a fatal error).
    assert (aah / ".migrated_from_xdg").exists()


def test_all_collision_no_moves_sentinel_still_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When all targets already exist, migrated=0, collided=3, sentinel written."""
    dirs = _seed_legacy(tmp_path, monkeypatch)
    aah = dirs["aah"]

    # Pre-create all three targets.
    (aah / "state").mkdir(parents=True)
    (aah / "cache").mkdir(parents=True)
    (aah / "config").mkdir(parents=True)

    result = maybe_migrate_legacy_xdg_storage()

    assert result.migrated == 0
    assert result.collided == 3
    # Sentinel still written so we don't retry forever.
    assert (aah / ".migrated_from_xdg").exists()


# ---------------------------------------------------------------------------
# 4. Idempotency: second call after success is a no-op
# ---------------------------------------------------------------------------


def test_idempotent_second_call_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Calling maybe_migrate_legacy_xdg_storage() twice: second call skips via sentinel."""
    _seed_legacy(tmp_path, monkeypatch)

    first = maybe_migrate_legacy_xdg_storage()
    second = maybe_migrate_legacy_xdg_storage()

    assert first.migrated == 3
    assert first.skipped is False

    assert second.skipped is True
    assert second.migrated == 0
    assert second.from_xdg is True


# ---------------------------------------------------------------------------
# 5. No legacy dirs → sentinel written, nothing moved
# ---------------------------------------------------------------------------


def test_no_legacy_dirs_writes_sentinel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no legacy dirs exist, the migration succeeds with migrated=0 and writes the sentinel."""
    aah = tmp_path / "aah"
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(aah))
    # XDG_*_HOME point to empty subtrees — no amplifier-agent subdir inside them.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "empty_state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty_cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_config"))

    result = maybe_migrate_legacy_xdg_storage()

    assert result.migrated == 0
    assert result.collided == 0
    assert result.skipped is False
    assert result.from_xdg is True
    assert (aah / ".migrated_from_xdg").exists()


# ---------------------------------------------------------------------------
# 6. amplifier_agent_home() is used as the target root
# ---------------------------------------------------------------------------


def test_migration_respects_amplifier_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Migration target root tracks AMPLIFIER_AGENT_HOME, not a hard-coded path."""
    custom_aah = tmp_path / "custom-home"
    legacy_state = tmp_path / "xdg_state" / "amplifier-agent"
    legacy_state.mkdir(parents=True)
    (legacy_state / "data.txt").write_text("legacy", encoding="utf-8")

    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(custom_aah))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty_cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_config"))

    result = maybe_migrate_legacy_xdg_storage()

    assert result.migrated == 1
    assert (custom_aah / "state" / "data.txt").read_text(encoding="utf-8") == "legacy"
    assert (custom_aah / ".migrated_from_xdg").exists()


# ---------------------------------------------------------------------------
# 7. file_lock is released after call (re-acquisition must not block)
# ---------------------------------------------------------------------------


def test_file_lock_released_after_migration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The migration lock file is released on return; re-acquiring it must not block."""
    _seed_legacy(tmp_path, monkeypatch)

    maybe_migrate_legacy_xdg_storage()

    lock_path = amplifier_agent_home() / ".migration.lock"
    assert lock_path.exists()
    # Acquiring the lock again must complete without hanging.
    with migration.file_lock(lock_path):
        pass  # If we reach here, the lock was successfully re-acquired.
