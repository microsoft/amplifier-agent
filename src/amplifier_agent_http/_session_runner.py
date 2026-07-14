"""Per-request AmplifierSession lifecycle for the HTTP face.

This module owns the bridge between an incoming HTTP request and the
amplifier kernel's per-turn execution. It mirrors -- intentionally, by
copy-and-adapt -- the body of ``amplifier_agent_lib._runtime.make_turn_handler``
but adapts the seams to the HTTP face's needs:

- Conversation comes from the request's ``messages[]``, not from a disk
  transcript via SessionStore.
- No IncrementalSaveHook -- the HTTP face is stateless; persistence is
  the host's job.
- Display events are pushed to an asyncio.Queue (HttpQueueDisplaySystem),
  not written to stderr (CliDisplaySystem).
- Approval is auto-accept (HttpAutoApprovalSystem) for the POC.

The POC reuses one ``PreparedBundle`` across all requests (loaded once at
lifespan startup) but creates a fresh ``AmplifierSession`` per turn -- the
same pattern the existing CLI uses. Session reuse with conversation reset
is in the v2 backlog under "D6 boot split".
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_agent_cli.provider_sources import inject_provider
from amplifier_agent_lib.bundle.hook_streaming import mount as mount_streaming_hook
from amplifier_agent_lib.bundle.host_tool_hook import mount as mount_host_tool_hook
from amplifier_agent_lib.bundle.host_tool_proxy import HostToolProxy
from amplifier_agent_lib.protocol_points.base import (
    ApprovalSystem,
    DisplaySystem,
)
from amplifier_agent_lib.spawn import hydrate_agent_overlay, spawn_sub_session
from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle  # type: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


# Serializes the per-request hook-config swap + create_session sequence.
# ``prepared.mount_plan`` is shared across all concurrent requests, but the
# hook-context-intelligence module's config is mutated transiently per request
# to reflect the effective workspace (which can vary per request when the
# X-Client-Session-Id header bridge is in play -- ``<base>`` for clients
# without correlation, ``<base>-<client-sid>`` when the header is present).
# The lock holds for microseconds (config swap + create_session); after
# create_session returns each session has its own mounted modules and the
# LLM call runs unblocked. Race-free without per-request PreparedBundle
# cloning. Scales to interactive cadence trivially; serialization becomes
# visible only at very high concurrent request rates.
_create_session_lock: asyncio.Lock = asyncio.Lock()


def hydrate_agent_configs(prepared: PreparedBundle) -> dict[str, dict[str, Any]]:
    """Pre-hydrate agent markdown overlays from the prepared bundle.

    Mirrors the cold-path setup in ``_runtime.make_turn_handler``. Each
    invocation of the runner re-uses the cached overlays, so this only runs
    once at lifespan startup.
    """
    mount_plan = prepared.mount_plan or {}
    agents = mount_plan.get("agents") or {}
    return {
        name: hydrate_agent_overlay(Path(entry["source_path"]))
        for name, entry in agents.items()
        if isinstance(entry, dict) and "source_path" in entry
    }


def _extract_host_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Unwrap OpenAI Chat Completions ``tools[]`` to the per-tool spec.

    Input shape (per OpenAI):

        [{"type": "function", "function": {"name", "description", "parameters"}}]

    Output shape (one dict per tool):

        [{"name", "description", "parameters"}]

    Skips entries that don't look like function tools or are missing a name.
    """
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "function":
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        out.append(
            {
                "name": name,
                "description": function.get("description", "") or "",
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


async def _mount_host_tool_proxies(
    coordinator: Any,
    host_tool_specs: list[dict[str, Any]],
) -> list[str]:
    """Register a ``HostToolProxy`` for each client-declared host tool.

    Returns the list of registered tool names (for the host_tool_hook's
    awareness set).
    """
    names: list[str] = []
    for spec in host_tool_specs:
        proxy = HostToolProxy(
            name=spec["name"],
            description=spec["description"],
            parameters=spec["parameters"],
        )
        await coordinator.mount("tools", proxy, name=proxy.name)
        names.append(proxy.name)
    return names


async def run_chat_turn(
    *,
    prepared: PreparedBundle,
    agent_configs: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    prompt: str,
    display: DisplaySystem,
    approval: ApprovalSystem,
    session_id: str | None = None,
    is_resumed: bool = False,
    tools: list[dict[str, Any]] | None = None,
    host_tool_yield_state: dict[str, Any] | None = None,
    workspace: str | None = None,
    provider_id: str = "anthropic",
    upstream_model: str | None = None,
) -> str:
    """Run one chat-completion turn against the prepared bundle.

    Parameters
    ----------
    prepared:
        The PreparedBundle loaded once at process start. Holds the mount plan
        (modules, hooks, providers) and the agent overlay metadata.
    agent_configs:
        Hydrated agent overlays. Pass the result of ``hydrate_agent_configs``
        once at startup; reusing the same dict across requests is safe.
    history:
        The conversation prior to this turn, in OpenAI-compatible message
        shape (``role``, ``content``, optional ``tool_calls``, etc.). Loaded
        into the context module via ``set_messages``. May be empty for the
        first turn of a new conversation.
    prompt:
        The current user prompt (the last user message's content). This is
        what ``session.execute()`` receives.
    display:
        The DisplaySystem implementation for this request. Typically an
        HttpQueueDisplaySystem whose queue is being drained concurrently.
    approval:
        The ApprovalSystem for this request. POC uses HttpAutoApprovalSystem.
    session_id:
        Optional session id. If not provided, a random one is generated. The
        kernel uses this to tag events; persistent storage is not the HTTP
        face's responsibility.
    is_resumed:
        Whether to pass ``is_resumed=True`` to ``prepared.create_session``.
        When ``True`` the kernel treats this as a continuation of a prior
        session (append-mode events.jsonl, etc.). Defaults to ``False`` for
        backward compatibility — callers that do not send ``X-Client-Session-Id``
        get a fresh session every turn as before.

    Returns
    -------
    str
        The assistant's reply text. Note that the same text has also been
        streamed via display events (``result/delta``); the return value is
        the final, complete assistant text.

    Raises
    ------
    Any exception raised by the kernel propagates. Cancellation
    (``asyncio.CancelledError``) propagates cleanly through the agent loop
    per the amplifier-expert audit.
    """
    sid = session_id or f"http-{uuid.uuid4().hex[:12]}"
    tid = f"turn-{uuid.uuid4().hex[:12]}"

    # Per-request mount-plan mutations: provider injection (routing) and
    # hook-context-intelligence workspace seeding (correlation). Both happen
    # under ``_create_session_lock`` because ``prepared.mount_plan`` is
    # process-wide shared state and we transiently mutate it for the duration
    # of ONE ``create_session`` call.
    #
    # Lock semantics:
    #   - Lock holds only for the swap + ``create_session`` call (microseconds
    #     in practice -- ``create_session`` does cold mounts, not I/O).
    #   - Once ``create_session`` returns, the session has its own coordinator
    #     with the mounted modules baked in; subsequent requests can proceed
    #     without disturbing it.
    #   - At interactive cadence the serialization point is invisible. At very
    #     high concurrent request rates the next step would be per-request
    #     ``PreparedBundle`` cloning -- on the design backlog.
    #
    # Provider injection (the routing surface):
    #   We replace ``prepared.mount_plan["providers"]`` with a single-entry
    #   list built by ``inject_provider(prepared, provider_id, model_override=
    #   upstream_model)``. ``provider_id`` is resolved at the chat_completions
    #   layer by looking the wire's ``model`` field up in
    #   ``app.state.served_models_registry`` (populated at lifespan from
    #   ``KNOWN_PROVIDERS``). On restore we reinstate whatever providers list
    #   was there at lifespan (empty list per the new lifespan -- providers
    #   are NOT pinned at lifespan now).
    #
    # Workspace seeding (the correlation surface):
    #   ``hook-context-intelligence`` consults its own config during
    #   ``session:start``, which fires INSIDE ``create_session`` -- before any
    #   ``coordinator.config`` writes can land. We re-seed the hook's config
    #   here so the per-request effective workspace (base or
    #   ``<base>-<client-sid>`` when the X-Client-Session-Id header is in
    #   play) reaches the hook in time.
    #
    # NOTE: We deliberately do NOT call ``prepare_bundle_for_session`` here.
    # The full prep (D4 mcp env + D5 merge_config + Fix C seed) was applied
    # ONCE at lifespan with the base workspace. We only need to re-seed Fix
    # C with the effective per-request workspace and inject the per-request
    # provider.
    async with _create_session_lock:
        # Save state to restore after create_session.
        saved_providers = list(prepared.mount_plan.get("providers") or [])
        hook_entry: dict[str, Any] | None = None
        saved_hook_cfg: dict[str, Any] | None = None

        # Provider injection for this request.
        prepared.mount_plan["providers"] = []
        inject_provider(prepared, provider_id, model_override=upstream_model)

        # Workspace seed for this request.
        if workspace:
            for entry in prepared.mount_plan.get("hooks") or []:
                if entry.get("module") == "hook-context-intelligence":
                    hook_entry = entry
                    saved_hook_cfg = entry.get("config")
                    new_cfg = dict(saved_hook_cfg or {})
                    new_cfg["project_slug"] = workspace
                    new_cfg["workspace"] = workspace
                    entry["config"] = new_cfg
                    break

        try:
            # Create a fresh session for this turn. The bundle (modules,
            # configs) was mounted once at startup; create_session is the
            # cheap per-turn factory. ``session:start`` fires INSIDE here,
            # with the hook reading our per-request seeded config and the
            # session mounting our per-request provider.
            session = await prepared.create_session(
                session_id=sid,
                session_cwd=Path.cwd(),
                is_resumed=is_resumed,
            )
        finally:
            # Restore the lifespan state so concurrent requests start from a
            # known-clean base. The lock serializes the swap window; nothing
            # can observe a half-mutated mount_plan.
            prepared.mount_plan["providers"] = saved_providers
            if hook_entry is not None:
                hook_entry["config"] = saved_hook_cfg

    # D5: write the workspace identity to coordinator.config. The
    # context-intelligence hook reads ``project_slug`` (ecosystem-canonical)
    # AND ``workspace`` (AAA-canonical) -- write both as aliases. This is
    # belt-and-suspenders on top of the lifespan Fix C pre-seed of the
    # hook's OWN module config: Fix C handles the first ``session:start``
    # event (fired INSIDE ``create_session``), the D5 writes here cover any
    # downstream hook that resolves the slug from the coordinator scope.
    # ``workspace`` is the resolved slug passed in by the HTTP face (resolved
    # at lifespan via ``resolve_workspace`` from the env-var chain).
    if workspace:
        session.coordinator.config["workspace"] = workspace
        session.coordinator.config["project_slug"] = workspace

    # Per-event default fields ensure every kernel event carries session_id
    # and turn_id for correlation in logs and on the wire.
    session.coordinator.hooks.set_default_fields(
        session_id=sid,
        turn_id=tid,
    )

    # Wire the protocol points as coordinator capabilities. The streaming
    # hook (mounted below) reads display.emit; tools/approval read
    # approval.request via WireApprovalProvider.
    session.coordinator.register_capability("display.emit", display.emit)
    wire_approval_provider = WireApprovalProvider(approval_request_fn=approval.request)
    session.coordinator.register_capability("approval.request", wire_approval_provider.request_approval)

    # Mount the vendored streaming hook -- translates kernel hooks to
    # display events. Without this, our HttpQueueDisplaySystem sees nothing.
    await mount_streaming_hook(session.coordinator, {})

    # Plumb client-declared host tools[]. For each entry we:
    # 1. Mount a HostToolProxy under the tool's name so the LLM can pick it.
    # 2. Mount the host_tool_hook with that name in the awareness set, so
    #    tool:pre events for these tools emit OpenAI-shape tool_calls/delta
    #    display events before the proxy raises HostToolYield.
    # Order matters: proxies BEFORE hook -- the hook reads tool_name from the
    # event payload and doesn't care about Tool object identity. Bundle tools
    # (delegate, todo, etc.) are unaffected since their names aren't in the
    # host_tools set.
    host_tool_specs = _extract_host_tools(tools)
    host_tool_names: list[str] = []
    if host_tool_specs:
        host_tool_names = await _mount_host_tool_proxies(session.coordinator, host_tool_specs)
        await mount_host_tool_hook(
            session.coordinator,
            {
                "host_tools": host_tool_names,
                # The yield_state dict (when provided by the HTTP face) is
                # written by the hook on tool:pre for a host tool. The HTTP
                # face reads this AFTER turn_task completes to decide whether
                # the terminal SSE chunk should have finish_reason=tool_calls.
                # Necessary because the kernel's session.execute() wraps any
                # exception (including our BaseException-derived HostToolYield)
                # as plain RuntimeError, losing the type signal.
                "yield_state": host_tool_yield_state,
            },
        )
        logger.info(
            "host-tool delegation enabled: %d tool(s) -- %s",
            len(host_tool_names),
            host_tool_names,
        )

    # Seed the conversation. The kernel's context module exposes set_messages
    # as a first-class Protocol method (per amplifier-expert audit Q3) with
    # explicit "session resume" semantics -- exactly the operation we need.
    if history:
        context_module = session.coordinator.get("context")
        if context_module is not None and hasattr(context_module, "set_messages"):
            await context_module.set_messages(history)
        else:
            logger.warning(
                "Conversation seeding skipped: context module %r has no set_messages",
                context_module,
            )

    # Register session.spawn so the `delegate` tool can spawn child sessions.
    # Required for amplifier bundles that use subagent delegation -- a core
    # part of the persona behavior we want to dogfood. Mirrors the closure
    # pattern in _runtime.py exactly.
    async def _spawn_fn(**kw: Any) -> dict[str, Any]:
        kw.setdefault("agent_configs", agent_configs)
        kw["parent_session"] = session
        return await spawn_sub_session(**kw)

    session.coordinator.register_capability("session.spawn", _spawn_fn)

    # Run the turn. ``async with session`` handles enter/exit hooks; if
    # cancelled mid-turn, CancelledError propagates through cleanly (per
    # amplifier-expert Q1) but the session's __aexit__ still fires.
    async with session:
        reply = await session.execute(prompt)
    return reply
