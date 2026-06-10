"""_runtime triggers migration once per process (D9)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_runtime_runs_migration_on_first_boot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # Reset the process-level guard so this test sees a "first boot".
    monkeypatch.setattr(_runtime, "_MIGRATION_RAN", False, raising=False)

    calls: list[int] = []
    monkeypatch.setattr(
        _runtime,
        "migrate_legacy_sessions_if_needed",
        lambda: calls.append(1) or SimpleNamespace(migrated=0, skipped=True, collided=0),
    )

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(return_value=_make_fake_session())

    handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="ws")
    await handler(_ctx())

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_runtime_skips_migration_on_subsequent_boots(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(_runtime, "_MIGRATION_RAN", False, raising=False)

    calls: list[int] = []
    monkeypatch.setattr(
        _runtime,
        "migrate_legacy_sessions_if_needed",
        lambda: calls.append(1) or SimpleNamespace(migrated=0, skipped=True, collided=0),
    )

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(side_effect=lambda **kw: _make_fake_session())

    handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="ws")
    await handler(_ctx())
    await handler(_ctx())  # second turn, same process

    assert len(calls) == 1, "migration must run at most once per process"
