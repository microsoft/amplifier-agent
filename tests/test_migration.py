"""Migration of the flat sessions/ tree to workspaces/_legacy/ (D9, §7).

All paths are computed inside migrate_legacy_sessions_if_needed() so the
XDG_STATE_HOME monkeypatch takes effect.
"""

from __future__ import annotations

import logging
from pathlib import Path

from amplifier_agent_lib import migration
from amplifier_agent_lib.persistence import state_root


def _seed_legacy_session(name: str, monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sess = state_root() / "sessions" / name
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "transcript.jsonl").write_text('{"role":"user"}', encoding="utf-8")
    return sess


def test_migration_moves_existing_sessions_to_legacy(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)

    result = migration.migrate_legacy_sessions_if_needed()

    assert result.migrated == 1
    assert result.skipped is False
    moved = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1" / "transcript.jsonl"
    assert moved.is_file()


def test_migration_brings_audit_subdirs_along(monkeypatch, tmp_path) -> None:
    """shutil.move carries audits/ verbatim — every per-session artifact moves (I8)."""
    sess = _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    audits = sess / "audits"
    audits.mkdir(parents=True, exist_ok=True)
    (audits / "turn-001.json").write_text('{"correlationId":"corr-1"}', encoding="utf-8")

    migration.migrate_legacy_sessions_if_needed()

    moved_audit = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1" / "audits" / "turn-001.json"
    assert moved_audit.is_file(), f"audit subdir not carried along to {moved_audit}"
    assert moved_audit.read_text(encoding="utf-8") == '{"correlationId":"corr-1"}'


def test_migration_is_idempotent(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)

    first = migration.migrate_legacy_sessions_if_needed()
    second = migration.migrate_legacy_sessions_if_needed()

    assert first.migrated == 1
    assert second.skipped is True
    assert second.migrated == 0


def test_migration_no_op_when_no_old_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    result = migration.migrate_legacy_sessions_if_needed()
    assert result.skipped is True
    assert result.migrated == 0


def test_migration_no_op_when_old_root_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (state_root() / "sessions").mkdir(parents=True, exist_ok=True)
    result = migration.migrate_legacy_sessions_if_needed()
    assert result.skipped is True
    assert result.migrated == 0


def test_migration_skips_target_collision_logs_warning(monkeypatch, tmp_path, caplog) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    # Pre-create the target so the move collides.
    target = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1"
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcript.jsonl").write_text("existing", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = migration.migrate_legacy_sessions_if_needed()

    assert result.collided == 1
    assert result.migrated == 0
    # Source is left in place (no data deletion, I6).
    assert (state_root() / "sessions" / "legacy-1").is_dir()
    assert any("already at target" in r.message for r in caplog.records)


def test_migration_removes_empty_old_root(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    migration.migrate_legacy_sessions_if_needed()
    assert not (state_root() / "sessions").exists()


def test_migration_preserves_old_root_if_not_empty(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    # A collision leaves a child behind, so the old root must NOT be removed.
    target = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1"
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcript.jsonl").write_text("existing", encoding="utf-8")

    migration.migrate_legacy_sessions_if_needed()

    assert (state_root() / "sessions").exists()


def test_migration_holds_flock_during_operation(monkeypatch, tmp_path) -> None:
    """The lock file is created under state_root and released after the call."""
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)

    migration.migrate_legacy_sessions_if_needed()

    lock_path = state_root() / ".migration.lock"
    assert lock_path.exists()
    # After return, the lock is releasable by another acquirer (kernel released
    # it on context exit). Acquiring it again must not block.
    with migration.file_lock(lock_path):
        pass
