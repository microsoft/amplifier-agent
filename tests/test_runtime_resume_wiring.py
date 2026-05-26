"""Unit tests for _runtime.py resume wiring via mount registry (A2 — CR-1).

Verifies two aspects of the resume/persistence wiring in make_turn_handler:

1. **Defect C** — wrong registry: the resume path and hook-registration path
   must use ``coordinator.get("context")`` (the module **mount** registry)
   rather than ``coordinator.get_capability("context.set_messages")`` (the
   capability registry).  ``context-simple`` mounts via ``coordinator.mount()``,
   not ``coordinator.register_capability()``, so the capability-registry path
   always returned ``None`` and both guards silently failed.

2. **Defect A** — hook event too narrow: the ``tool:post`` hook only fires
   when a tool is invoked.  Pure conversational turns (no tool calls) never
   trigger it, so the transcript was never persisted after a chat-only turn.
   Fix: explicit turn-end save mirrors the ``amplifier-app-cli`` main_loop
   pattern.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.engine import TurnContext


def _ctx(session_id: str = "sess-mount-test", prompt: str = "hello") -> TurnContext:
    return TurnContext(
        session_id=session_id,
        turn_id="t-1",
        prompt=prompt,
        approval=MagicMock(),
        display=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_context_stub() -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Return (context_stub, set_messages_mock, get_messages_mock)."""
    context_stub = MagicMock()
    set_messages_mock: AsyncMock = AsyncMock()
    get_messages_mock: AsyncMock = AsyncMock(return_value=[])
    context_stub.set_messages = set_messages_mock
    context_stub.get_messages = get_messages_mock
    return context_stub, set_messages_mock, get_messages_mock


def _make_prepared_for_coordinator(coordinator: Any) -> MagicMock:
    """Return a PreparedBundle mock whose session uses the given coordinator."""
    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.coordinator = coordinator

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}

    async def _create(**kwargs: Any) -> MagicMock:
        return session_mock

    prepared.create_session = _create
    return prepared


# ---------------------------------------------------------------------------
# Test 1 — Defect C: resume path must use mount registry, not capability registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_wiring_uses_mount_registry_for_set_messages(
    tmp_path, monkeypatch
) -> None:
    """Resume path must call coordinator.get('context').set_messages(transcript).

    The broken pattern — ``coordinator.get_capability('context.set_messages')``
    — returns ``None`` for ``context-simple`` because that module mounts via
    ``coordinator.mount()``, not ``coordinator.register_capability()``.  When
    ``None`` is returned the guard silently fails and the transcript is never
    replayed; the resumed session starts with an empty context.

    Status before fix:  FAILS — ``set_messages_mock`` never awaited.
    Status after fix:   PASSES.
    """
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.session_store import SessionStore

    session_id = "sess-mount-resume"
    transcript = [
        {"role": "user", "content": "my color is purple"},
        {"role": "assistant", "content": "noted"},
    ]
    SessionStore(tmp_path).save(session_id, transcript, metadata={"last_tool": ""})
    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    context_stub, set_messages_mock, _ = _make_context_stub()

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub      # mount registry — correct path
    coordinator.get_capability.return_value = None   # capability registry — empty

    prepared = _make_prepared_for_coordinator(coordinator)

    handler = make_turn_handler(prepared, cwd=None, is_resumed=True)
    await handler(_ctx(session_id=session_id))

    set_messages_mock.assert_awaited_once_with(transcript)


# ---------------------------------------------------------------------------
# Test 2 — Defect C: hook registration must use mount registry, not capability registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_registration_uses_mount_registry_for_get_messages(
    tmp_path, monkeypatch
) -> None:
    """IncrementalSaveHook must receive the bound ``get_messages`` method from
    the mount registry, not from the capability registry.

    The broken pattern — ``coordinator.get_capability('context.get_messages')``
    — returns ``None``, causing the guard to fail silently so the hook is never
    registered.  Without the hook, tool-call transcripts are not persisted.

    Status before fix:  FAILS — no ``tool:post/incremental_save`` registration.
    Status after fix:   PASSES.
    """
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.incremental_save import IncrementalSaveHook

    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    context_stub, _, get_messages_mock = _make_context_stub()

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub
    coordinator.get_capability.return_value = None

    captured: list[dict[str, Any]] = []

    def _capture_register(event: str, fn: Any, *, name: str = "") -> None:
        captured.append({"event": event, "handler": fn, "name": name})

    coordinator.hooks.register.side_effect = _capture_register

    prepared = _make_prepared_for_coordinator(coordinator)

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    await handler(_ctx(session_id="sess-hook-test"))

    tool_post = [
        r for r in captured
        if r["event"] == "tool:post" and "incremental_save" in r["name"]
    ]
    assert len(tool_post) >= 1, (
        "Expected 'tool:post/incremental_save' to be registered via mount registry; "
        f"got registrations: {captured}"
    )
    hook = tool_post[0]["handler"]
    assert isinstance(hook, IncrementalSaveHook), (
        f"Registered handler must be IncrementalSaveHook; got {type(hook).__name__}"
    )
    assert hook._get_messages is get_messages_mock, (
        "IncrementalSaveHook._get_messages must be context_stub.get_messages "
        "(mount-registry bound method), not a capability-registry callable."
    )


# ---------------------------------------------------------------------------
# Test 3 — Fix 2 (Defect A): explicit turn-end save persists transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_end_save_persists_transcript_after_execute(
    tmp_path, monkeypatch
) -> None:
    """Turn-end save must call context.get_messages() and store.save() after
    session.execute() completes, regardless of whether any tools were invoked.

    This covers the pure-conversational-turn case: no ``tool:post`` events fire
    during a chat-only exchange, so the IncrementalSaveHook never runs.  The
    explicit turn-end save mirrors the ``amplifier-app-cli`` main_loop pattern
    and closes Defect A.

    Status before Fix 2: FAILS — get_messages never called, transcript not saved.
    Status after Fix 2:  PASSES.
    """
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.session_store import SessionStore

    session_id = "sess-turn-end-save"
    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    final_transcript = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    context_stub, _, get_messages_mock = _make_context_stub()
    get_messages_mock.return_value = final_transcript

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub
    coordinator.get_capability.return_value = None

    prepared = _make_prepared_for_coordinator(coordinator)

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    reply = await handler(_ctx(session_id=session_id))

    assert reply == "reply"

    # context.get_messages() must have been called at turn end.
    assert get_messages_mock.await_count >= 1, (
        "context.get_messages() must be called at turn end to persist the "
        f"transcript; await_count={get_messages_mock.await_count}"
    )

    # The transcript must have been written to disk.
    stored = SessionStore(tmp_path).load(session_id)
    assert stored is not None, "Session transcript must be persisted after turn completes"
    saved_transcript, _ = stored
    assert saved_transcript == final_transcript, (
        f"Persisted transcript must match context.get_messages() return value; "
        f"got: {saved_transcript!r}"
    )
