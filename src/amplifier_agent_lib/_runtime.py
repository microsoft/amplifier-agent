"""Runtime bridge — make_turn_handler factory.

Creates a TurnHandler closed over a PreparedBundle that creates a fresh
AmplifierSession per turn (one-shot stateful via logical replay; OpenClaw pattern).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_agent_lib.engine import TurnContext, TurnHandler

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle


def make_turn_handler(
    prepared: PreparedBundle,
    *,
    cwd: str | None,
    is_resumed: bool,
) -> TurnHandler:
    """Return a TurnHandler closed over the loaded PreparedBundle.

    The returned coroutine creates a fresh AmplifierSession per turn
    (one-shot stateful via logical replay; OpenClaw pattern), registers the
    ``session.spawn`` capability so the ``delegate`` tool can spawn sub-agents,
    and returns the model reply.

    Parameters
    ----------
    prepared:
        The loaded PreparedBundle to use for each turn.
    cwd:
        Optional working directory string.  Resolved to an absolute Path
        if provided; None otherwise.
    is_resumed:
        Whether the session should be treated as a resumed session.

    Returns
    -------
    TurnHandler
        Async callable that accepts a TurnContext and returns a reply string.
    """
    from amplifier_agent_lib.spawn import hydrate_agent_overlay, spawn_sub_session

    resolved_cwd: Path | None = Path(cwd).resolve() if cwd else None

    # Pre-hydrate agent overlays from the vendored agent markdown files.
    # This is done once at handler-creation time (cold path) so each turn
    # pays no I/O cost.  The overlay dicts are closed over in the handler.
    #
    # prepared.mount_plan["agents"] has shape:
    #   {"explorer": {"name": "explorer", "source_path": "/path/explorer.md"}, ...}
    # after bundle/loader.py enriches the agent entries with source_path.
    agent_configs: dict[str, dict[str, Any]] = {
        name: hydrate_agent_overlay(Path(entry["source_path"]))
        for name, entry in (prepared.mount_plan.get("agents") or {}).items()
        if isinstance(entry, dict) and "source_path" in entry
    }

    async def handler(ctx: TurnContext) -> str:
        session_id = ctx.session_id if ctx.session_id else None
        session = await prepared.create_session(
            session_id=session_id,
            session_cwd=resolved_cwd,
            is_resumed=is_resumed,
        )

        # Register session.spawn on the coordinator so the delegate tool can
        # spawn child sessions.  Per KERNEL_PHILOSOPHY, this is app-layer
        # policy: the kernel provides the mechanism (coordinator capabilities),
        # the app layer provides the policy (which agents exist, how they're
        # configured, and how they inherit parent state).
        #
        # The closure captures the pre-hydrated agent_configs and the live
        # session object.  Each invocation of _spawn_fn sets
        # kw["parent_session"] = session so the spawner always uses the
        # currently-running session as the parent.
        async def _spawn_fn(**kw: Any) -> dict[str, Any]:
            kw.setdefault("agent_configs", agent_configs)
            kw["parent_session"] = session
            return await spawn_sub_session(**kw)

        session.coordinator.register_capability("session.spawn", _spawn_fn)

        async with session:
            return await session.execute(ctx.prompt)

    return handler
