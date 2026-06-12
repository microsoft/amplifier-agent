"""Migration helpers for amplifier-agent storage layout transitions.

Two migrations live here:

  1. migrate_legacy_sessions_if_needed()
     One-shot move of the flat sessions/ tree to workspaces/_legacy/.
     Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (D9, §7).
     Lazy, idempotent, flock-guarded. Runs on the first AAA boot after upgrade.

  2. maybe_migrate_legacy_xdg_storage()
     One-shot move of the XDG-era storage roots into ~/.amplifier-agent/.
     Design: docs/designs/2026-06-11-drop-xdg-and-flag-cleanup.md (Phase 1).
     Triggered by `amplifier-agent update` after a successful reinstall.
     NOT triggered on engine startup — users who skip update continue with
     the new layout starting empty; their legacy tree sits unused until
     they run update.

Unix-only (fcntl.flock). AAA targets Linux/macOS; Windows is out of scope.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from amplifier_agent_lib.persistence import _home, amplifier_agent_home, state_root

logger = logging.getLogger(__name__)

LEGACY_WORKSPACE = "_legacy"


@dataclass
class MigrationResult:
    """Outcome of a migration attempt."""

    migrated: int = 0
    skipped: bool = False
    collided: int = 0
    from_xdg: bool = False


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


# ---------------------------------------------------------------------------
# XDG → ~/.amplifier-agent/ migration (Phase 1)
# ---------------------------------------------------------------------------


def _legacy_state() -> Path:
    """Pre-refactor state path (honors XDG_STATE_HOME for users who set it).

    Intentionally retains XDG_STATE_HOME reads so we can find the old data.
    This function is only used as a one-shot legacy-source resolver.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else _home() / ".local" / "state") / "amplifier-agent"


def _legacy_cache() -> Path:
    """Pre-refactor cache path (honors XDG_CACHE_HOME for users who set it).

    Intentionally retains XDG_CACHE_HOME reads so we can find the old data.
    This function is only used as a one-shot legacy-source resolver.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else _home() / ".cache") / "amplifier-agent"


def _legacy_config() -> Path:
    """Pre-refactor config path (honors XDG_CONFIG_HOME for users who set it).

    Intentionally retains XDG_CONFIG_HOME reads so we can find the old data.
    This function is only used as a one-shot legacy-source resolver.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else _home() / ".config") / "amplifier-agent"


def maybe_migrate_legacy_xdg_storage() -> MigrationResult:
    """One-way move of XDG-era storage into ~/.amplifier-agent/.

    Idempotent via sentinel file at <home>/.migrated_from_xdg.
    Concurrent-safe via fcntl.flock on lock file.
    Never destroys data (I6): if a target directory already exists, the
    legacy source is left in place and counted as a collision.

    Moves (skips if target already exists):
      $XDG_STATE_HOME/amplifier-agent/  (or ~/.local/state/amplifier-agent/)
          -> <home>/state/
      $XDG_CACHE_HOME/amplifier-agent/  (or ~/.cache/amplifier-agent/)
          -> <home>/cache/
      $XDG_CONFIG_HOME/amplifier-agent/ (or ~/.config/amplifier-agent/)
          -> <home>/config/

    The XDG_* reads in the _legacy_*() helpers are intentionally retained as
    legacy-source resolvers for this one-shot migration.  After this function
    runs once (sentinel written), XDG vars are never consulted again.

    Returns a MigrationResult with from_xdg=True to distinguish this
    migration from the workspace sessions migration.
    """
    home = amplifier_agent_home()
    sentinel = home / ".migrated_from_xdg"

    if sentinel.exists():
        logger.debug("xdg-migration: sentinel exists, skipping")
        return MigrationResult(skipped=True, from_xdg=True)

    home.mkdir(parents=True, exist_ok=True)
    lock_path = home / ".migration.lock"

    with file_lock(lock_path):
        # Re-check under lock (concurrent-process race).
        if sentinel.exists():
            return MigrationResult(skipped=True, from_xdg=True)

        sources: list[tuple[Path, str]] = [
            (_legacy_state(), "state"),
            (_legacy_cache(), "cache"),
            (_legacy_config(), "config"),
        ]

        moved_pairs: list[tuple[Path, Path]] = []
        collided = 0

        for legacy_path, target_subdir in sources:
            target = home / target_subdir
            if not legacy_path.exists():
                logger.debug("xdg-migration: %s does not exist, skipping", legacy_path)
                continue
            if target.exists():
                logger.warning(
                    "xdg-migration: target %s already exists; leaving %s in place (collision)",
                    target,
                    legacy_path,
                )
                collided += 1
                continue
            logger.info("xdg-migration: moving %s -> %s", legacy_path, target)
            shutil.move(str(legacy_path), str(target))
            moved_pairs.append((legacy_path, target))

        # Write sentinel only on full completion (partial failures leave it
        # absent so the next invocation retries the remaining moves).
        ts = datetime.now(tz=UTC).isoformat()
        moved_summary = [str(p[0]) for p in moved_pairs]
        sentinel.write_text(
            f"migrated at {ts}\nfrom: {moved_summary}\n",
            encoding="utf-8",
        )
        logger.info(
            "xdg-migration: complete — moved %d dirs, %d collisions",
            len(moved_pairs),
            collided,
        )
        return MigrationResult(
            migrated=len(moved_pairs),
            skipped=False,
            collided=collided,
            from_xdg=True,
        )
