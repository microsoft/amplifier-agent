"""spawn.py — library-internal sub-agent spawning (Phase 1 stub).

Design references
-----------------
* §8 of aaa-v2-design-checkpoint.md: spawn is LIBRARY-INTERNAL — no adapter
  override surface, no spawn_fn parameter on any public API.  Sub-agents are
  in-process AmplifierSession instances within the parent engine's process, NOT
  new subprocesses.  This mirrors Brian's D3 directive: external callers must
  never be given a hook to substitute their own spawn logic.
* OpenClaw's CLISpawnManager precedent: the manager encapsulates all spawn
  decisions internally; CLI adapters receive only a session handle, never a
  factory or callable to override.

Phase 1 ships the module boundary as a stub.  Full implementation lands in
Phase 4 alongside amplifier-foundation integration.

Phase 4 notes (for future implementer)
---------------------------------------
* Resolve a PreparedBundle from the config dict.
* Merge child config from parent config (provider selection, tool grants, …).
* Instantiate a child AmplifierSession with parent_id=parent_session_id.
* Wire cancellation propagation so cancelling the parent also cancels children.
"""

from __future__ import annotations

from typing import Any

__all__ = ["InternalSpawnManager", "SpawnNotReadyError"]


class SpawnNotReadyError(RuntimeError):
    """Raised when spawn is invoked before Phase 4 integration is in place."""


class InternalSpawnManager:
    """Library-internal manager for spawning child AmplifierSession instances.

    No public external API.  Callers receive a session handle only — there is
    no spawn_fn parameter or override hook exposed to adapters or end-users.
    """

    def __init__(self) -> None:
        pass

    def spawn_session(self, *, parent_session_id: str, config: dict[str, Any]) -> Any:
        """Spawn a child AmplifierSession in-process.

        Parameters
        ----------
        parent_session_id:
            The session ID of the parent (calling) session.
        config:
            Configuration dict for the child session (provider selection,
            tool grants, bundle reference, etc.).

        Raises
        ------
        SpawnNotReadyError
            Always in Phase 1.  Real implementation lands in Phase 4.
        """
        raise SpawnNotReadyError(
            "InternalSpawnManager.spawn_session is a Phase 1 stub; "
            "real implementation lands in Phase 4 alongside amplifier-foundation integration."
        )
