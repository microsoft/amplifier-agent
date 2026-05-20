"""Tests for spawn.py — InternalSpawnManager stub (Phase 1).

Verifies:
1. InternalSpawnManager constructs successfully.
2. spawn_session raises SpawnNotReadyError.
3. Module __all__ is exactly {'InternalSpawnManager', 'SpawnNotReadyError'} —
   guards against future spawn_fn parameter or external override hooks.
"""

from __future__ import annotations

import pytest


def test_internal_spawn_manager_constructs() -> None:
    """InternalSpawnManager() must be constructable (isinstance check)."""
    from amplifier_agent_lib.spawn import InternalSpawnManager

    mgr = InternalSpawnManager()
    assert isinstance(mgr, InternalSpawnManager)


def test_spawn_session_raises_spawn_not_ready_error() -> None:
    """spawn_session must raise SpawnNotReadyError (Phase 1 stub behaviour)."""
    from amplifier_agent_lib.spawn import InternalSpawnManager, SpawnNotReadyError

    mgr = InternalSpawnManager()
    with pytest.raises(SpawnNotReadyError):
        mgr.spawn_session(parent_session_id="test-parent-id", config={})


def test_spawn_module_all_is_exact() -> None:
    """spawn.__all__ must be exactly {'InternalSpawnManager', 'SpawnNotReadyError'}.

    Guards against inadvertently adding spawn_fn or any other external-API name.
    """
    import amplifier_agent_lib.spawn as spawn_module

    assert set(spawn_module.__all__) == {"InternalSpawnManager", "SpawnNotReadyError"}
