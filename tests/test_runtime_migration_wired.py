"""_runtime no longer triggers migration automatically (removed in favour of
`amplifier-agent migrate` standalone subcommand).

Migration is user-invoked only. These tests assert that the runtime handler
does NOT call migrate_legacy_sessions_if_needed on any turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_agent_lib import _runtime
from amplifier_agent_lib.engine import TurnContext


class _FakeContextModule:
    async def get_messages(self) -> list[dict[str, Any]]:
        return []


def _make_fake_session() -> SimpleNamespace:
    coordinator = SimpleNamespace(
        config={},
        hooks=SimpleNamespace(set_default_fields=lambda **kw: None, register=lambda *a, **k: None),
        register_capability=lambda *a, **k: None,
        get=lambda key: _FakeContextModule() if key == "context" else None,
    )
    session = MagicMock()
    session.coordinator = coordinator
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value="reply")
    return session


def _ctx() -> TurnContext:
    return TurnContext(
        session_id="sid-1",
        turn_id="turn-1",
        prompt="hi",
        approval=MagicMock(),
        display=MagicMock(),
    )


def test_runtime_does_not_import_migration_symbol() -> None:
    """After the refactor, _runtime no longer exposes migrate_legacy_sessions_if_needed
    or the _MIGRATION_RAN process guard."""
    assert not hasattr(_runtime, "migrate_legacy_sessions_if_needed"), (
        "_runtime must not import migrate_legacy_sessions_if_needed"
    )
    assert not hasattr(_runtime, "_MIGRATION_RAN"), "_runtime must not have the _MIGRATION_RAN process-level guard"


@pytest.mark.asyncio
async def test_runtime_does_not_run_migration_on_first_turn(monkeypatch, tmp_path) -> None:
    """The turn handler no longer calls migrate_legacy_sessions_if_needed."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(return_value=_make_fake_session())

    with patch("amplifier_agent_lib.migration.migrate_legacy_sessions_if_needed") as mock_fn:
        handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="ws")
        await handler(_ctx())

    mock_fn.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_does_not_run_migration_on_subsequent_turns(monkeypatch, tmp_path) -> None:
    """Across multiple turns, migrate_legacy_sessions_if_needed is never called."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(side_effect=lambda **kw: _make_fake_session())

    with patch("amplifier_agent_lib.migration.migrate_legacy_sessions_if_needed") as mock_fn:
        handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="ws")
        await handler(_ctx())
        await handler(_ctx())  # second turn, same process

    mock_fn.assert_not_called()
