"""One-shot migration of the legacy flat sessions/ tree to workspaces/_legacy/.

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (D9, §7).

Lazy, idempotent, flock-guarded. Runs on the first AAA boot after upgrade.
Moves every pre-existing ``state_root()/sessions/<id>/`` into
``state_root()/workspaces/_legacy/sessions/<id>/``. Never deletes data (I6):
on a target collision the source is left in place and counted.

Unix-only (fcntl.flock). AAA targets Linux/macOS; Windows is out of scope.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from amplifier_agent_lib.persistence import state_root

logger = logging.getLogger(__name__)

LEGACY_WORKSPACE = "_legacy"


@dataclass
class MigrationResult:
    """Outcome of a migration attempt."""

    migrated: int = 0
    skipped: bool = False
    collided: int = 0


@contextlib.contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive flock on ``lock_path`` for the duration of the block.

    The lock file is created if absent. The kernel releases the lock when the
    file descriptor closes (on context exit or process death), so a killed
    process never strands the lock.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def migrate_legacy_sessions_if_needed() -> MigrationResult:
    """Move the flat sessions/ tree to workspaces/_legacy/ if present (D9).

    Returns a MigrationResult. ``skipped=True`` means there was nothing to do
    (no old root, or it was empty). Idempotent: a second call after a complete
    migration returns ``skipped=True``.
    """
    root = state_root()
    old_root = root / "sessions"
    if not old_root.exists() or not any(old_root.iterdir()):
        logger.debug("migration: no legacy sessions/ to migrate")
        return MigrationResult(migrated=0, skipped=True)

    new_root = root / "workspaces" / LEGACY_WORKSPACE / "sessions"
    lock_path = root / ".migration.lock"

    with file_lock(lock_path):
        # Re-check after acquiring the lock (concurrent-boot race, §7).
        if not old_root.exists() or not any(old_root.iterdir()):
            return MigrationResult(migrated=0, skipped=True)

        logger.info("migration: starting legacy sessions/ -> workspaces/_legacy/")
        new_root.mkdir(parents=True, exist_ok=True)
        moved, collided = 0, 0
        for session_dir in old_root.iterdir():
            if not session_dir.is_dir():
                continue
            target = new_root / session_dir.name
            if target.exists():
                logger.warning("migration: %s already at target; leaving in place", session_dir.name)
                collided += 1
                continue
            shutil.move(str(session_dir), str(target))
            moved += 1

        # Remove the old root only if nothing was left behind (no deletion, I6).
        with contextlib.suppress(OSError):
            old_root.rmdir()

        logger.info("migration: moved %d sessions to _legacy (%d collisions)", moved, collided)
        return MigrationResult(migrated=moved, skipped=False, collided=collided)
