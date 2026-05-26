"""Tests for _runtime.py — make_turn_handler factory.

Six async tests covering:
1. handler returns stub reply, execute awaited once with prompt, session_id and is_resumed forwarded
2. session_cwd resolved to tmp_path.resolve()
3. empty string session_id becomes None
4. is_resumed=True propagates to create_session
5. session.spawn capability registered on session.coordinator
6. display.emit and approval.request registered as coordinator capabilities
7. coordinator.hooks.set_default_fields called with session_id=ctx.session_id
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.engine import TurnContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_prepared(reply: str = "stub reply") -> tuple[MagicMock, AsyncMock]:
    """Return (prepared_mock, execute_mock).

    prepared_mock.create_session is an async function that returns a session
    mock (which is also an async context manager) with execute = AsyncMock.

    ``session_mock.coordinator.get.return_value`` is set to ``None`` so the
    turn-end save path (Fix 2) skips gracefully without needing a real context
    module.  Tests that specifically want a context module should configure
    ``coordinator.get.return_value`` after obtaining the session mock.
    """
    execute_mock = AsyncMock(return_value=reply)
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    # Return None from mount registry so turn-end save is a no-op for tests
    # that don't need persistence behaviour.
    session_mock.coordinator.get.return_value = None

    async def _fake_create_session(**kwargs):
        return session_mock

    prepared_mock = MagicMock()
    prepared_mock.create_session = _fake_create_session

    return prepared_mock, execute_mock


def _ctx(session_id: str = "s", prompt: str = "hello") -> TurnContext:
    """Build a minimal TurnContext."""
    return TurnContext(
        session_id=session_id,
        turn_id="t-1",
        prompt=prompt,
        approval=MagicMock(),
        display=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Test 1: handler calls create_session and execute, returns reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_calls_create_session_and_execute() -> None:
    """Handler returns stub reply; execute awaited once with prompt;
    session_id == 's', is_resumed == False."""
    prepared, execute_mock = _fake_prepared("stub reply")
    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)

    result = await handler(_ctx(session_id="s", prompt="hello"))

    assert result == "stub reply"
    execute_mock.assert_awaited_once_with("hello")


# ---------------------------------------------------------------------------
# Test 2: session_cwd is resolved from cwd argument
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_passes_session_cwd_resolved(tmp_path) -> None:
    """session_cwd passed to create_session equals tmp_path.resolve()."""
    captured_kwargs: dict = {}

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    # No context module — skip turn-end save gracefully.
    session_mock.coordinator.get.return_value = None

    async def _capturing_create_session(**kwargs):
        captured_kwargs.update(kwargs)
        return session_mock

    prepared = MagicMock()
    prepared.create_session = _capturing_create_session

    handler = make_turn_handler(prepared, cwd=str(tmp_path), is_resumed=False)
    await handler(_ctx())

    assert captured_kwargs["session_cwd"] == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Test 3: empty string session_id becomes None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_empty_session_id_becomes_none() -> None:
    """Empty string session_id is mapped to None before passing to create_session."""
    captured_kwargs: dict = {}

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock

    async def _capturing_create_session(**kwargs):
        captured_kwargs.update(kwargs)
        return session_mock

    prepared = MagicMock()
    prepared.create_session = _capturing_create_session

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    await handler(_ctx(session_id=""))

    assert captured_kwargs["session_id"] is None


# ---------------------------------------------------------------------------
# Test 4: is_resumed=True propagates to create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_passes_is_resumed() -> None:
    """is_resumed=True propagates to create_session kwargs."""
    captured_kwargs: dict = {}

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    # No context module — skip turn-end save gracefully.
    session_mock.coordinator.get.return_value = None

    async def _capturing_create_session(**kwargs):
        captured_kwargs.update(kwargs)
        return session_mock

    prepared = MagicMock()
    prepared.create_session = _capturing_create_session

    handler = make_turn_handler(prepared, cwd=None, is_resumed=True)
    await handler(_ctx())

    assert captured_kwargs["is_resumed"] is True


# ---------------------------------------------------------------------------
# Test 5: session.spawn capability is registered on session.coordinator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_registers_session_spawn_capability() -> None:
    """make_turn_handler must register 'session.spawn' on session.coordinator
    after create_session returns and before session.execute is called.
    """
    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.config = {}  # empty — no agents to hydrate
    # No context module — skip turn-end save gracefully.
    session_mock.coordinator.get.return_value = None

    async def _fake_create_session(**kwargs):
        return session_mock

    prepared_mock = MagicMock()
    prepared_mock.create_session = _fake_create_session
    # No agents in mount_plan → hydrate loop is a no-op
    prepared_mock.mount_plan = {"agents": {}}

    handler = make_turn_handler(prepared_mock, cwd=None, is_resumed=False)
    await handler(_ctx())

    # coordinator.register_capability must have been called with 'session.spawn'
    calls = session_mock.coordinator.register_capability.call_args_list
    spawn_calls = [c for c in calls if c.args and c.args[0] == "session.spawn"]
    assert len(spawn_calls) == 1, (
        f"Expected exactly one 'session.spawn' registration; got {len(spawn_calls)}.  All calls: {calls}"
    )


# ---------------------------------------------------------------------------
# Fake helpers for capability registration / default-fields tests
# ---------------------------------------------------------------------------


class _FakeHooks:
    """Captures set_default_fields kwargs for assertion."""

    def __init__(self) -> None:
        self.set_default_args: dict[str, Any] = {}

    def set_default_fields(self, **kw: Any) -> None:
        self.set_default_args.update(kw)

    def register(self, event: str, handler: Any, *, name: str = "") -> None:
        """No-op stub — allows programmatic hook mounts to succeed in unit tests."""


class _FakeCoordinator:
    """Captures register_capability calls; exposes _FakeHooks as .hooks."""

    def __init__(self) -> None:
        self.captured_caps: dict[str, Any] = {}
        self.hooks = _FakeHooks()

    def register_capability(self, name: str, fn: Any) -> None:
        self.captured_caps[name] = fn

    def get_capability(self, name: str) -> Any:
        """Return ``None`` for unknown capabilities (matches the real
        coordinator contract for absent capabilities)."""
        return self.captured_caps.get(name)

    def get(self, name: str) -> Any:
        """Return ``None`` — no modules mounted in this stub coordinator.

        Matches the real coordinator's mount-registry API.  Returning ``None``
        causes the runtime's context-access guards to skip gracefully, keeping
        tests that don't need a context module free of noise.
        """
        return None


def _fake_prepared_with_coordinator(coordinator: Any, reply: str = "ok") -> MagicMock:
    """Return a prepared mock whose session has the given coordinator."""
    execute_mock = AsyncMock(return_value=reply)
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.coordinator = coordinator

    async def _fake_create_session(**kwargs: Any) -> MagicMock:
        return session_mock

    prepared_mock = MagicMock()
    prepared_mock.create_session = _fake_create_session
    prepared_mock.mount_plan = {"agents": {}}
    return prepared_mock


# ---------------------------------------------------------------------------
# Test 6: display.emit and approval.request registered as coordinator capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_turn_handler_registers_display_emit_as_capability() -> None:
    """make_turn_handler must register 'display.emit' and 'approval.request'
    as coordinator capabilities after create_session returns."""
    coordinator = _FakeCoordinator()
    prepared = _fake_prepared_with_coordinator(coordinator, reply="ok")
    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)

    result = await handler(_ctx())

    assert result == "ok"
    assert "display.emit" in coordinator.captured_caps, (
        f"Expected 'display.emit' in captured_caps; got {list(coordinator.captured_caps)}"
    )
    assert "approval.request" in coordinator.captured_caps, (
        f"Expected 'approval.request' in captured_caps; got {list(coordinator.captured_caps)}"
    )


# ---------------------------------------------------------------------------
# Test 7: coordinator.hooks.set_default_fields called with session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_turn_handler_sets_default_fields_session_id() -> None:
    """coordinator.hooks.set_default_fields must be called with
    session_id equal to ctx.session_id ('sess-9')."""
    coordinator = _FakeCoordinator()
    prepared = _fake_prepared_with_coordinator(coordinator, reply="ok")
    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)

    ctx = TurnContext(
        session_id="sess-9",
        turn_id="t-1",
        prompt="hello",
        approval=MagicMock(),
        display=MagicMock(),
    )
    await handler(ctx)

    assert coordinator.hooks.set_default_args.get("session_id") == "sess-9", (
        f"Expected session_id='sess-9'; got {coordinator.hooks.set_default_args}"
    )


# ---------------------------------------------------------------------------
# Test 8: Resumed session — handler loads transcript and calls
#         context.set_messages with it (A2 — CR-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_loads_transcript_for_resumed_session(tmp_path, monkeypatch) -> None:
    """When is_resumed=True and SessionStore has a saved transcript for the
    session_id, the handler must load it and call
    ``coordinator.get('context').set_messages(transcript)`` (A2 — CR-1).

    Uses the module mount registry (coordinator.get), not the capability
    registry (coordinator.get_capability), because context-simple mounts via
    coordinator.mount(), not coordinator.register_capability().
    """
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.session_store import SessionStore

    # Pre-populate the store on disk so the handler can find it.
    session_id = "sess-resume-test"
    transcript = [
        {"role": "user", "content": "earlier message"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    SessionStore(tmp_path).save(session_id, transcript, metadata={"last_tool": ""})

    # Make _runtime.state_root() return tmp_path so its SessionStore
    # looks at the same root.
    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    # Context stub exposed via mount registry (coordinator.get("context")).
    set_messages_mock = AsyncMock()
    get_messages_mock = AsyncMock(return_value=[])
    context_stub = MagicMock()
    context_stub.set_messages = set_messages_mock
    context_stub.get_messages = get_messages_mock

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub      # mount registry — correct path
    coordinator.get_capability.return_value = None   # capability registry — empty

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.coordinator = coordinator

    async def _fake_create_session(**kwargs: Any) -> MagicMock:
        return session_mock

    prepared = MagicMock()
    prepared.create_session = _fake_create_session
    prepared.mount_plan = {"agents": {}}

    handler = make_turn_handler(prepared, cwd=None, is_resumed=True)
    await handler(_ctx(session_id=session_id))

    set_messages_mock.assert_awaited_once_with(transcript)


# ---------------------------------------------------------------------------
# Test 9: Handler registers IncrementalSaveHook on tool:post (A2 — CR-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_registers_incremental_save_hook(tmp_path, monkeypatch) -> None:
    """Handler must register an ``incremental_save`` hook on the ``tool:post``
    event so transcripts are persisted after every tool invocation."""
    import amplifier_agent_lib._runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    captured_registrations: list[dict[str, Any]] = []

    def fake_register(event: str, handler_fn: Any, *, name: str = "") -> None:
        captured_registrations.append({"event_name": event, "handler": handler_fn, "name": name})

    # Context stub via mount registry so hook registration succeeds and the
    # turn-end save (Fix 2) can call get_messages() without a TypeError.
    get_messages_mock = AsyncMock(return_value=[])
    context_stub = MagicMock()
    context_stub.set_messages = AsyncMock()
    context_stub.get_messages = get_messages_mock

    coordinator = MagicMock()
    coordinator.get.return_value = context_stub       # mount registry
    coordinator.get_capability.return_value = None    # capability registry — empty
    coordinator.hooks.register.side_effect = fake_register

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.coordinator = coordinator

    async def _fake_create_session(**kwargs: Any) -> MagicMock:
        return session_mock

    prepared = MagicMock()
    prepared.create_session = _fake_create_session
    prepared.mount_plan = {"agents": {}}

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    await handler(_ctx(session_id="sess-save-test"))

    incremental = [
        r for r in captured_registrations if r["event_name"] == "tool:post" and "incremental_save" in r["name"]
    ]
    assert len(incremental) >= 1, (
        "Expected at least one 'tool:post' registration with 'incremental_save' "
        f"in the name; got: {captured_registrations}"
    )


# ---------------------------------------------------------------------------
# Test 10: Handler registers WireApprovalProvider as approval.request capability
#          (A3 — CR-2, Design §4.8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_registers_wire_approval_provider(tmp_path, monkeypatch) -> None:
    """The capability registered under 'approval.request' must be a bound
    method of a :class:`WireApprovalProvider` instance — not the raw
    ``ctx.approval.request`` callable.  Per design §4.8 (A3 — CR-2)."""
    import amplifier_agent_lib._runtime as runtime_mod
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    monkeypatch.setattr(runtime_mod, "state_root", lambda: tmp_path)

    captured: dict[str, Any] = {}

    def capture(name: str, fn: Any) -> None:
        captured[name] = fn

    coordinator = MagicMock()
    coordinator.register_capability.side_effect = capture
    coordinator.get_capability.return_value = None
    coordinator.get.return_value = None  # no context module — skip turn-end save

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.coordinator = coordinator

    async def _fake_create_session(**kwargs: Any) -> MagicMock:
        return session_mock

    prepared = MagicMock()
    prepared.create_session = _fake_create_session
    prepared.mount_plan = {"agents": {}}

    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    await handler(_ctx())

    registered_approval_capability = captured.get("approval.request")
    assert registered_approval_capability is not None, (
        f"Expected 'approval.request' to be registered; got: {list(captured)}"
    )
    assert hasattr(registered_approval_capability, "__self__"), (
        "Expected registered approval capability to be a bound method "
        f"(have __self__); got: {registered_approval_capability!r}"
    )
    assert isinstance(registered_approval_capability.__self__, WireApprovalProvider), (
        "Expected bound method's __self__ to be a WireApprovalProvider; "
        f"got: {type(registered_approval_capability.__self__).__name__}"
    )
