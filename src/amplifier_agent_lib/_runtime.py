"""Runtime bridge — make_turn_handler factory and handle_initialize entry point.

``make_turn_handler`` creates a TurnHandler closed over a PreparedBundle that
creates a fresh AmplifierSession per turn (one-shot stateful via logical
replay; OpenClaw pattern).

``handle_initialize`` is the wire-side entry point that loads the prepared
bundle, forwards a wire-supplied ``mcpConfigPath`` to ``tool-mcp`` via
``AMPLIFIER_MCP_CONFIG``, and stores ``host.capabilities`` on
``session.metadata``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_foundation.session import diagnose_transcript, repair_transcript

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.config import merge_config
from amplifier_agent_lib.engine import TurnContext, TurnHandler
from amplifier_agent_lib.incremental_save import IncrementalSaveHook
from amplifier_agent_lib.persistence import state_root
from amplifier_agent_lib.session_store import SessionStore
from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

logger = logging.getLogger(__name__)


def prepare_bundle_for_session(
    prepared: PreparedBundle,
    *,
    host_config: dict[str, Any] | None,
    workspace: str,
) -> None:
    """Apply host_config and workspace seeding to ``prepared.mount_plan`` in place.

    Single source of truth for the three bundle-prep transforms every face
    (CLI's ``make_turn_handler``, HTTP face's lifespan + per-request runner)
    runs at the bundle-mount seam. Previously each face inlined the same
    sequence and ``_session_runner.py`` was self-documenting as a
    "copy-and-adapt" of ``make_turn_handler``'s prep block. This function
    is the canonical extraction.

    Three steps in this exact order:

    1. **D4: mcp.configPath -> env**
       If ``host_config["mcp"]["configPath"]`` is a non-empty string, write
       it to ``AMPLIFIER_MCP_CONFIG``. ``tool-mcp`` reads this env var
       through its own discovery chain. Hosts that prefer to set
       ``AMPLIFIER_MCP_CONFIG`` directly in the engine process environment
       can skip the key entirely.

    2. **D5: merge_config overlay**
       ``merge_config`` expects ``{module_id: config_dict}``, but
       ``mount_plan["tools"|"hooks"|"providers"]`` are LISTS of
       ``{module, config, source}`` dicts (the shape
       ``Bundle.to_mount_plan()`` produces). Build the ``{module_id:
       config_dict}`` view, run the merge, then write the merged values
       back into the SAME list entries in place so the kernel sees the
       overrides at ``mount_plan["tools"][n]["config"]`` etc.

    3. **Fix C: hook-context-intelligence workspace seed**
       Pre-seed ``project_slug`` (and ``workspace`` alias) into the
       ``hook-context-intelligence`` module's OWN config so the hook
       resolves the correct workspace slug when ``session:start`` fires
       INSIDE ``create_session()`` -- which runs BEFORE the post-create
       ``coordinator.config`` writes can land. The hook's resolution
       chain is::

           config['project_slug']                       (this seed wins)
           -> coordinator.config['project_slug']        (post-create write)
           -> slugified session.working_dir             (bundle install dir)
           -> 'default'

       Without this seed, session:start lands in the wrong on-disk
       bucket because at that moment ``coordinator.config['project_slug']``
       is still unset.

    Mutation semantics (v1): mutates ``prepared.mount_plan`` in place and
    writes ``os.environ``. Callers that need an isolated mount plan (e.g.
    HTTP face per-request prep with a per-request workspace) must clone
    the prepared bundle's mount_plan BEFORE calling. A future clone-return
    variant is on the design backlog.
    """
    # D4: mcp.configPath -> AMPLIFIER_MCP_CONFIG env var.
    mcp_block = (host_config or {}).get("mcp")
    if isinstance(mcp_block, dict):
        config_path_from_host = mcp_block.get("configPath")
        if isinstance(config_path_from_host, str) and config_path_from_host:
            os.environ["AMPLIFIER_MCP_CONFIG"] = config_path_from_host

    # D5: merge_config overlay onto mount_plan tools/hooks/providers.
    mount_plan: dict[str, Any] = prepared.mount_plan or {}
    bundle_module_configs: dict[str, dict[str, Any]] = {}
    for section in ("tools", "hooks", "providers"):
        for entry in mount_plan.get(section) or []:
            mid = entry.get("module")
            if mid:
                bundle_module_configs[mid] = dict(entry.get("config") or {})

    merged_modules, _allow_skew = merge_config(
        bundle_modules=bundle_module_configs,
        host_config=host_config,
    )

    for section in ("tools", "hooks", "providers"):
        for entry in mount_plan.get(section) or []:
            mid = entry.get("module")
            if mid and mid in merged_modules:
                entry["config"] = merged_modules[mid]

    # Fix C: pre-seed workspace into hook-context-intelligence's own config.
    for entry in mount_plan.get("hooks") or []:
        if entry.get("module") == "hook-context-intelligence":
            hook_cfg = dict(entry.get("config") or {})
            hook_cfg["project_slug"] = workspace
            hook_cfg["workspace"] = workspace
            entry["config"] = hook_cfg
            break


def _repair_loaded_transcript_if_needed(
    loaded_transcript: list[dict],
    *,
    session_id: str,
    store: SessionStore,
) -> list[dict]:
    """Diagnose and repair a transcript loaded from disk before replay.

    Mirrors the app-cli pattern (PR #156 + PR #146, microsoft/amplifier-app-cli):
    sessions that were interrupted mid-tool-call (Ctrl+C, SIGKILL, OOM, MCP
    drops) can persist orphaned ``tool_calls`` with no matching ``tool``
    result, ordering violations, or incomplete assistant turns.  Replaying
    such a transcript causes providers (notably Anthropic) to reject the
    next LLM call with a 400 because every ``tool_use`` must have a paired
    ``tool_result``.

    The repair runs the foundation's ``diagnose_transcript`` /
    ``repair_transcript`` (Layer Level 1, pure, ``<10ms``) against the loaded
    entries.  Healthy transcripts pass through unchanged.  Broken ones get
    synthetic results / assistant responses injected, the cleaned transcript
    is **persisted back to disk** so the next ``--resume`` starts clean even
    if the current turn also fails, and a ``logger.warning`` records the
    failure modes for operator visibility.

    Foundation's diagnostic prefers ``line_num`` (1-based) annotations for
    the ``incomplete_turns`` fallback path; SessionStore does not annotate
    them, so we add them to shallow copies before diagnosing.  Repair's
    output strips ``line_num`` via ``_strip_line_num`` so callers receive
    clean dicts; we pass the unannotated originals through on the healthy
    path so no input is mutated.

    Parameters
    ----------
    loaded_transcript:
        The transcript as returned by ``SessionStore.load`` — a list of
        OpenAI-style message dicts (``role``, ``content``, optional
        ``tool_calls``, ``tool_call_id``).  Empty lists are returned as-is.
    session_id:
        The session ID, used for write-back via ``store.save`` and for
        log correlation.
    store:
        The SessionStore instance — reused for write-back so a single
        ``state_root`` lookup serves both the load and the persist.

    Returns
    -------
    list[dict]
        The original list if the transcript was healthy, or the repaired
        list (with ``line_num`` stripped) if any failure mode was detected.
    """
    if not loaded_transcript:
        return loaded_transcript

    annotated = [{**entry, "line_num": idx + 1} for idx, entry in enumerate(loaded_transcript)]

    diagnosis = diagnose_transcript(annotated)
    if diagnosis["status"] == "healthy":
        return loaded_transcript

    repaired = repair_transcript(annotated, diagnosis)

    logger.warning(
        "Resumed session %s had a broken transcript — repaired before replay. "
        "failure_modes=%s orphaned_tool_ids=%s misplaced_tool_ids=%s "
        "incomplete_turns=%d entries_before=%d entries_after=%d",
        session_id,
        diagnosis["failure_modes"],
        diagnosis["orphaned_tool_ids"],
        diagnosis["misplaced_tool_ids"],
        len(diagnosis["incomplete_turns"]),
        len(loaded_transcript),
        len(repaired),
    )

    # Write-back: persist the repaired transcript so the next --resume
    # starts clean even if this turn also fails.  Mirrors PR #146.
    try:
        store.save(session_id, repaired, metadata={"last_turn": "repaired"})
    except Exception:
        # Write-back failure is non-fatal — the in-memory repair still lets
        # this turn proceed.  Log and continue so a flaky disk doesn't
        # prevent recovery.
        logger.exception(
            "Failed to persist repaired transcript for session %s; "
            "in-memory repair will still be replayed for this turn.",
            session_id,
        )

    return repaired


def make_turn_handler(
    prepared: PreparedBundle,
    *,
    cwd: str | None,
    is_resumed: bool,
    host_config: dict[str, Any] | None = None,
    workspace: str | None = None,
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
    host_config:
        Optional host-config dict (as loaded by load_config). When it carries
        ``mcp.configPath``, the engine forwards the path to ``tool-mcp`` via
        ``AMPLIFIER_MCP_CONFIG`` so the module loads it via its standard
        config-discovery priority chain (see
        ``amplifier_module_tool_mcp/config.py``). Hosts that prefer can set
        ``AMPLIFIER_MCP_CONFIG`` directly in the engine's process environment
        instead; ``tool-mcp`` reads it natively.
    workspace:
        Optional workspace slug from the CLI ``--workspace`` flag (D1).
        Resolved once at handler-creation time via
        ``persistence.resolve_workspace`` (argv > env > cwd, D2).  The
        resolved slug is written to ``coordinator.config`` as both
        ``"workspace"`` (AAA-canonical) and ``"project_slug"``
        (ecosystem-canonical alias, D5) and determines the
        ``SessionStore`` root (D8).

    Returns
    -------
    TurnHandler
        Async callable that accepts a TurnContext and returns a reply string.
    """
    from amplifier_agent_lib.bundle.hook_streaming import mount as mount_streaming_hook
    from amplifier_agent_lib.persistence import resolve_workspace
    from amplifier_agent_lib.spawn import hydrate_agent_overlay, spawn_sub_session

    resolved_cwd: Path | None = Path(cwd).resolve() if cwd else None

    # Resolve the workspace identity once (cold path). argv > env > cwd (D2).
    # The resolved slug buckets all session state for this handler's turns and
    # is written to coordinator.config inside the handler (D5).
    resolved_workspace = resolve_workspace(
        argv_workspace=workspace,
        env=os.environ,
        cwd=resolved_cwd if resolved_cwd is not None else Path.cwd(),
    )
    # D8: workspace root is state_root()/workspaces/<slug>. Using the module-
    # level state_root() name (not workspaces_root() from persistence) so that
    # test-time monkeypatching of state_root propagates through correctly.
    workspace_root = state_root() / "workspaces" / resolved_workspace

    # Apply the bundle-prep transforms: mcp.configPath → env (D4),
    # merge_config overlay onto mount_plan (D5), and Fix C hook-context-
    # intelligence workspace seed. See ``prepare_bundle_for_session`` for
    # the canonical order + rationale; that helper is the single source of
    # truth shared by every face (CLI here, HTTP face's lifespan + per-
    # request runner). Mutates ``prepared.mount_plan`` in place.
    prepare_bundle_for_session(
        prepared,
        host_config=host_config,
        workspace=resolved_workspace,
    )

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
        # D8: bucket all session state under the per-workspace root.
        store = SessionStore(workspace_root)
        loaded_transcript: list[dict] | None = None
        if session_id and is_resumed:
            loaded = store.load(session_id)
            if loaded is not None:
                loaded_transcript, _ = loaded
                # Diagnose + repair the on-disk transcript before replay.
                # Sessions interrupted mid-tool-call (Ctrl+C, SIGKILL, OOM,
                # MCP drops) can persist orphaned tool_calls; replaying them
                # makes the next provider call reject with a 400.  Mirrors
                # microsoft/amplifier-app-cli PR #156 (pre-turn repair) +
                # PR #146 (resume-time repair) — collapsed into one site
                # because amplifier-agent is single-process-per-turn so
                # "load from disk" IS the pre-turn operation.
                loaded_transcript = _repair_loaded_transcript_if_needed(
                    loaded_transcript,
                    session_id=session_id,
                    store=store,
                )

        session = await prepared.create_session(
            session_id=session_id,
            session_cwd=resolved_cwd,
            is_resumed=is_resumed,
        )

        # D5: write workspace identity to coordinator.config. project_slug is
        # the ecosystem-canonical alias every existing hook reads; workspace is
        # the AAA-canonical name. Written as aliases (I4) until the ecosystem
        # aligns on one.
        session.coordinator.config["workspace"] = resolved_workspace
        session.coordinator.config["project_slug"] = resolved_workspace

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

    Loads the prepared bundle from cache, forwards a wire-supplied
    ``params["mcpConfigPath"]`` to ``tool-mcp`` via ``AMPLIFIER_MCP_CONFIG``,
    and stores ``params.host.capabilities`` on ``session.metadata`` for
    future capability-flag logic without wire-protocol changes.

    Parameters
    ----------
    params:
        An ``InitializeParams``-shaped dict.  Reads ``sessionId``, ``resume``,
        ``mcpConfigPath``, and ``host.capabilities``.

    Returns
    -------
    The created session.

    Notes
    -----
    The wrapper writes the MCP config file in the format the module expects
    (see amplifier-module-tool-mcp config.py). The engine just sets
    ``AMPLIFIER_MCP_CONFIG`` so the module's ``_load_from_env`` picks it up
    during mount alongside the bundle's static tool-mcp config.
    See methods.py for the wire-protocol field.
    """
    prepared = await load_and_prepare_cached(aaa_version=__version__)

    session_id: str | None = params.get("sessionId") or None
    is_resumed: bool = bool(params.get("resume", False))

    # ── A5: Q9 — forward wire-supplied MCP config path to tool-mcp ──
    # The wrapper writes the file in the format the module expects; the
    # engine just sets the env var so the module's _load_from_env picks
    # it up during mount. See methods.py for the wire-protocol field.
    _wire_mcp_config_path = params.get("mcpConfigPath") or None
    if _wire_mcp_config_path:
        os.environ["AMPLIFIER_MCP_CONFIG"] = _wire_mcp_config_path

    session = await prepared.create_session(
        session_id=session_id,
        is_resumed=is_resumed,
    )

    return session
