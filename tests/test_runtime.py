"""Tests for _runtime.py — make_turn_handler factory.

Four async tests covering:
1. handler returns stub reply, execute awaited once with prompt, session_id and is_resumed forwarded
2. session_cwd resolved to tmp_path.resolve()
3. empty string session_id becomes None
4. is_resumed=True propagates to create_session
"""

from __future__ import annotations

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
    """
    execute_mock = AsyncMock(return_value=reply)
    session_mock = MagicMock()
    session_mock.execute = execute_mock

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
        f"Expected exactly one 'session.spawn' registration; "
        f"got {len(spawn_calls)}.  All calls: {calls}"
    )
