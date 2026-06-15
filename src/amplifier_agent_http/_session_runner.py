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

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_agent_lib.bundle.hook_streaming import mount as mount_streaming_hook
from amplifier_agent_lib.protocol_points.base import (
    ApprovalSystem,
    DisplaySystem,
)
from amplifier_agent_lib.spawn import hydrate_agent_overlay, spawn_sub_session
from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle  # type: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


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


async def run_chat_turn(
    *,
    prepared: PreparedBundle,
    agent_configs: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    prompt: str,
    display: DisplaySystem,
    approval: ApprovalSystem,
    session_id: str | None = None,
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

    # Create a fresh session for this turn. The bundle (modules, configs) was
    # mounted once at startup; create_session is the cheap per-turn factory.
    session = await prepared.create_session(
        session_id=sid,
        session_cwd=None,  # POC: bundle uses its own default cwd
        is_resumed=False,
    )

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
