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
async def test_resume_wiring_uses_mount_registry_for_set_messages(tmp_path, monkeypatch) -> None:
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
    # D8: data lives under tmp_path/workspaces/<workspace>/sessions/<id>/
    SessionStore(tmp_path / "workspaces" / "test-ws").save(session_id, transcript, metadata={"last_tool": ""})
    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    context_stub, set_messages_mock, _ = _make_context_stub()

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub  # mount registry — correct path
    coordinator.get_capability.return_value = None  # capability registry — empty

    prepared = _make_prepared_for_coordinator(coordinator)

    handler = make_turn_handler(prepared, cwd=None, is_resumed=True, workspace="test-ws")
    await handler(_ctx(session_id=session_id))

    set_messages_mock.assert_awaited_once_with(transcript)


# ---------------------------------------------------------------------------
# Test 2 — Defect C: hook registration must use mount registry, not capability registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_registration_uses_mount_registry_for_get_messages(tmp_path, monkeypatch) -> None:
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

    tool_post = [r for r in captured if r["event"] == "tool:post" and "incremental_save" in r["name"]]
    assert len(tool_post) >= 1, (
        f"Expected 'tool:post/incremental_save' to be registered via mount registry; got registrations: {captured}"
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


# ---------------------------------------------------------------------------
# Test 4 — Pre-replay repair: broken on-disk transcript is repaired before
#                            being handed to context.set_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_repair_applied_to_broken_transcript(tmp_path, monkeypatch) -> None:
    """An on-disk transcript with an orphaned tool_call must be repaired
    before ``context.set_messages`` is called.

    Sessions interrupted mid-tool-call (Ctrl+C, SIGKILL, OOM, MCP drops) can
    persist an assistant message with ``tool_calls`` but no matching ``tool``
    result.  Replaying that into a fresh context would make the next
    provider call reject with a 400 ("each tool_use must have a paired
    tool_result").  Mirrors microsoft/amplifier-app-cli PR #156 + PR #146.

    This test confirms that the repair fires on the resume path: the
    transcript handed to ``set_messages`` must contain a synthetic
    ``tool_result`` for the orphaned id — proving the fix runs upstream of
    the replay step.

    Status before fix: FAILS — set_messages receives the raw broken transcript.
    Status after fix:  PASSES — set_messages receives the repaired transcript.
    """
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.session_store import SessionStore

    session_id = "sess-resume-repair"

    # Persist a broken transcript: assistant tool_call with no paired tool result.
    broken_transcript = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_resume_orphan", "tool": "ls", "input": {}},
            ],
        },
        # NB: no tool message with tool_call_id="call_resume_orphan" —
        # the interrupt happened between assistant emission and tool result.
    ]
    # D8: data lives under tmp_path/workspaces/<workspace>/sessions/<id>/
    SessionStore(tmp_path / "workspaces" / "test-ws").save(session_id, broken_transcript, metadata={"last_tool": ""})
    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    context_stub, set_messages_mock, _ = _make_context_stub()

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub
    coordinator.get_capability.return_value = None

    prepared = _make_prepared_for_coordinator(coordinator)

    handler = make_turn_handler(prepared, cwd=None, is_resumed=True, workspace="test-ws")
    await handler(_ctx(session_id=session_id))

    # set_messages must have been awaited exactly once.
    set_messages_mock.assert_awaited_once()
    await_args = set_messages_mock.await_args
    assert await_args is not None, "await_args populated by assert_awaited_once"
    replayed_transcript: list[dict] = await_args.args[0]

    # The replayed transcript must NOT be the raw broken list — repair
    # injected a synthetic tool result for the orphaned id.
    assert replayed_transcript != broken_transcript, (
        f"Repair did not run before replay: set_messages received the raw broken transcript: {replayed_transcript!r}"
    )
    assert len(replayed_transcript) > len(broken_transcript), (
        f"Repair should have appended a synthetic tool result; got: {replayed_transcript!r}"
    )

    # A synthetic tool result with the orphaned id must be present.
    synthetic_tool_results = [
        e for e in replayed_transcript if e.get("role") == "tool" and e.get("tool_call_id") == "call_resume_orphan"
    ]
    assert len(synthetic_tool_results) == 1, (
        "Expected exactly one synthetic tool result for call_resume_orphan in "
        f"replayed transcript; got: {replayed_transcript!r}"
    )

    # Sanity: no line_num annotations leaked into the replay payload.
    for entry in replayed_transcript:
        assert "line_num" not in entry, f"line_num leaked into transcript handed to set_messages: {entry!r}"


# ---------------------------------------------------------------------------
# Test 5 — Repair must NOT run when is_resumed=False (fresh session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_repair_on_fresh_session(tmp_path, monkeypatch) -> None:
    """On a non-resume invocation, the on-disk transcript must be ignored —
    the session starts fresh.  Diagnose/repair must not touch it even if it
    is broken; that is the whole point of "not resuming".

    The clean signal: set_messages must never be called on a fresh session.
    """
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.session_store import SessionStore

    session_id = "sess-fresh-not-resumed"
    # Put a broken transcript on disk; a fresh (non-resume) run must skip it.
    broken_transcript = [
        {"role": "user", "content": "old turn"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_old_orphan", "tool": "ls", "input": {}},
            ],
        },
    ]
    SessionStore(tmp_path).save(session_id, broken_transcript, metadata={"last_tool": ""})
    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    context_stub, set_messages_mock, _ = _make_context_stub()

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub
    coordinator.get_capability.return_value = None

    prepared = _make_prepared_for_coordinator(coordinator)

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    await handler(_ctx(session_id=session_id))

    # No replay on a fresh session => no repair path triggered either.
    set_messages_mock.assert_not_called()


@pytest.mark.asyncio
async def test_turn_end_save_persists_transcript_after_execute(tmp_path, monkeypatch) -> None:
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

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False, workspace="test-ws")
    reply = await handler(_ctx(session_id=session_id))

    assert reply == "reply"

    # context.get_messages() must have been called at turn end.
    assert get_messages_mock.await_count >= 1, (
        "context.get_messages() must be called at turn end to persist the "
        f"transcript; await_count={get_messages_mock.await_count}"
    )

    # The transcript must have been written to disk.
    # D8: data lives under tmp_path/workspaces/<workspace>/sessions/<id>/
    stored = SessionStore(tmp_path / "workspaces" / "test-ws").load(session_id)
    assert stored is not None, "Session transcript must be persisted after turn completes"
    saved_transcript, _ = stored
    assert saved_transcript == final_transcript, (
        f"Persisted transcript must match context.get_messages() return value; got: {saved_transcript!r}"
    )
