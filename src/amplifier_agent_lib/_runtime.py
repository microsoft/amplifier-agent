"""Runtime bridge — make_turn_handler factory.

Creates a TurnHandler closed over a PreparedBundle that creates a fresh
AmplifierSession per turn (one-shot stateful via logical replay; OpenClaw pattern).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_agent_lib.engine import TurnContext, TurnHandler
from amplifier_agent_lib.incremental_save import IncrementalSaveHook
from amplifier_agent_lib.persistence import state_root
from amplifier_agent_lib.session_store import SessionStore

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
    (one-shot stateful via logical replay; OpenClaw pattern), wires
    ``ctx.display.emit`` and ``ctx.approval.request`` into the coordinator
    as capabilities, sets per-turn default event fields on the hooks system,
    registers the ``session.spawn`` capability so the ``delegate`` tool can
    spawn sub-agents, and returns the model reply.

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
    from amplifier_agent_lib.bundle.hook_streaming import mount as mount_streaming_hook
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

        # Build the SessionStore once per turn.  If the session is being
        # resumed, attempt to load a previously persisted transcript so it
        # can be replayed into the new session via ``context.set_messages``.
        store = SessionStore(state_root())
        loaded_transcript: list[dict] | None = None
        if session_id and is_resumed:
            loaded = store.load(session_id)
            if loaded is not None:
                loaded_transcript, _ = loaded

        session = await prepared.create_session(
            session_id=session_id,
            session_cwd=resolved_cwd,
            is_resumed=is_resumed,
        )

        # Wire display and approval into the coordinator so hook events can
        # flow back to the client.  Per SC-1, set default event fields so
        # every kernel event carries session_id and turn_id automatically.
        session.coordinator.hooks.set_default_fields(
            session_id=ctx.session_id,
            turn_id=ctx.turn_id,
        )
        session.coordinator.register_capability("display.emit", ctx.display.emit)
        session.coordinator.register_capability("approval.request", ctx.approval.request)

        # Mount the vendored streaming hook programmatically.  It lives inside this
        # wheel rather than at a git URL, so we bypass foundation's URI resolver
        # entirely and register the hook handlers directly on the coordinator.
        # Matches the canonical pattern in amplifier-app-cli/main.py:2551.
        await mount_streaming_hook(session.coordinator, {})

        # Resume: replay the persisted transcript into the new session's
        # context module via the ``context.set_messages`` capability.  Guard
        # with ``is not None`` so kernels/contexts without this capability
        # simply skip replay rather than crash (A2 — CR-1, Design §4.8).
        if loaded_transcript:
            set_messages = session.coordinator.get_capability("context.set_messages")
            if set_messages is not None:
                await set_messages(loaded_transcript)

        # Persistence: register the IncrementalSaveHook on ``tool:post`` so the
        # transcript is checkpointed after every tool call.  Skip if the
        # session has no id (no place to persist) or if the context module
        # does not expose ``context.get_messages`` (nothing to read).
        if session_id:
            get_messages = session.coordinator.get_capability("context.get_messages")
            if get_messages is not None:
                save_hook = IncrementalSaveHook(
                    store=store,
                    session_id=session_id,
                    get_messages=get_messages,
                )
                session.coordinator.hooks.register(
                    "tool:post", save_hook, name="incremental_save"
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
