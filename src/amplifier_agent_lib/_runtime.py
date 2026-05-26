"""Runtime bridge — make_turn_handler factory and handle_initialize entry point.

``make_turn_handler`` creates a TurnHandler closed over a PreparedBundle that
creates a fresh AmplifierSession per turn (one-shot stateful via logical
replay; OpenClaw pattern).

``handle_initialize`` is the wire-side entry point that loads the prepared
bundle, threads wire-supplied ``mcpServers`` into ``tool-mcp.mount()`` via
``tool_overrides``, and stores ``host.capabilities`` on ``session.metadata``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.engine import TurnContext, TurnHandler
from amplifier_agent_lib.incremental_save import IncrementalSaveHook
from amplifier_agent_lib.persistence import state_root
from amplifier_agent_lib.session_store import SessionStore
from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

logger = logging.getLogger(__name__)


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
        wire_approval_provider = WireApprovalProvider(approval_request_fn=ctx.approval.request)
        session.coordinator.register_capability("approval.request", wire_approval_provider.request_approval)

        # Mount the vendored streaming hook programmatically.  It lives inside this
        # wheel rather than at a git URL, so we bypass foundation's URI resolver
        # entirely and register the hook handlers directly on the coordinator.
        # Matches the canonical pattern in amplifier-app-cli/main.py:2551.
        await mount_streaming_hook(session.coordinator, {})

        # Resume: replay the persisted transcript into the new session's
        # context module via ``coordinator.get("context").set_messages``.
        # Uses the module **mount** registry (coordinator.get), not the
        # capability registry (coordinator.get_capability), because
        # context-simple mounts via coordinator.mount(), not
        # coordinator.register_capability().  Guard with hasattr so any
        # context module that does not expose set_messages is skipped safely
        # rather than crashing (A2 — CR-1, Design §4.8).
        if loaded_transcript:
            context_module = session.coordinator.get("context")
            if context_module is not None and hasattr(context_module, "set_messages"):
                await context_module.set_messages(loaded_transcript)
            else:
                logger.warning(
                    "Resume requested for session %s but context module does not "
                    "expose set_messages — transcript replay skipped. "
                    "Context module: %r",
                    session_id,
                    context_module,
                )

        # Persistence: register the IncrementalSaveHook on ``tool:post`` so the
        # transcript is checkpointed after every tool call.  Skip if the
        # session has no id (no place to persist) or if the context module
        # does not expose ``get_messages`` (nothing to read).
        # Uses mount registry for the same reason as the resume path above.
        if session_id:
            context_module = session.coordinator.get("context")
            if context_module is not None and hasattr(context_module, "get_messages"):
                save_hook = IncrementalSaveHook(
                    store=store,
                    session_id=session_id,
                    get_messages=context_module.get_messages,
                )
                session.coordinator.hooks.register("tool:post", save_hook, name="incremental_save")
            else:
                logger.warning(
                    "Session %s: context module does not expose get_messages — "
                    "IncrementalSaveHook will not be registered. "
                    "Context module: %r",
                    session_id,
                    context_module,
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
            reply = await session.execute(ctx.prompt)
            # Persist final transcript for resume continuity (mirrors
            # amplifier-app-cli main_loop).  IncrementalSaveHook handles
            # crash recovery after every tool call; this explicit save
            # handles conversational turns with no tool calls — those never
            # emit ``tool:post``, so the hook never fires.
            if session_id:
                context_module = session.coordinator.get("context")
                if context_module is not None and hasattr(context_module, "get_messages"):
                    final_transcript = await context_module.get_messages()
                    store.save(
                        session_id,
                        final_transcript,
                        metadata={"last_turn": "complete"},
                    )
        return reply

    return handler


async def handle_initialize(params: dict[str, Any]) -> Any:
    """Wire-side initialize entry point.

    Loads the prepared bundle from cache, threads wire-supplied
    ``params["mcpServers"]`` into ``tool-mcp.mount()`` via ``tool_overrides``,
    and stores ``params.host.capabilities`` on ``session.metadata`` for
    future capability-flag logic without wire-protocol changes.

    Parameters
    ----------
    params:
        An ``InitializeParams``-shaped dict.  Reads ``sessionId``, ``resume``,
        ``mcpServers``, and ``host.capabilities``.

    Returns
    -------
    The created session.

    Notes
    -----
    The static ``tool-mcp`` config (e.g. ``verbose_servers``, ``max_content_size``)
    declared in the bundle is merged with the dynamic ``servers`` dict supplied
    over the wire.  The combined dict is passed to ``mount()`` with highest
    priority per ``amplifier_module_tool_mcp/config.py``.
    """
    prepared = await load_and_prepare_cached(aaa_version=__version__)

    session_id: str | None = params.get("sessionId") or None
    is_resumed: bool = bool(params.get("resume", False))

    # ── A5: Q9 — thread MCP servers into tool-mcp.mount() ──
    # PreparedBundle stubs are incomplete; .config is the merged bundle yaml.
    _tool_mcp_static = (
        prepared.config.get("tools", {}).get("tool-mcp", {}).get("config", {})  # pyright: ignore[reportAttributeAccessIssue]
    )
    tool_mcp_config = {**_tool_mcp_static, "servers": params.get("mcpServers") or {}}

    # ``tool_overrides`` is accepted by create_session per amplifier_module_tool_mcp/config.py:35-53,56-61
    # — the config dict passed to mount() has highest priority.
    session = await prepared.create_session(
        session_id=session_id,
        is_resumed=is_resumed,
        tool_overrides={"tool-mcp": {"config": tool_mcp_config}},  # pyright: ignore[reportCallIssue]
    )

    # ── A5: host capabilities storage ──
    session.metadata["host_capabilities"] = (params.get("host") or {}).get("capabilities") or {}

    return session
