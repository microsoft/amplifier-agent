"""_runtime wires resolve_workspace into the hot path (D5, D6, D8).

The handler must:
  - write coordinator.config["workspace"] and ["project_slug"] (both the alias)
  - construct SessionStore with root = state_root()/workspaces/<workspace>
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib import _runtime
from amplifier_agent_lib.persistence import state_root


class _FakeContextModule:
    async def get_messages(self) -> list[dict[str, Any]]:
        return []


def _make_fake_session() -> SimpleNamespace:
    """A fake AmplifierSession exposing the surface the handler touches."""
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
    session.execute = AsyncMock(return_value="reply-text")
    return session


def _make_prepared(fake_session) -> MagicMock:
    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(return_value=fake_session)
    return prepared


@pytest.mark.asyncio
async def test_runtime_writes_workspace_to_coordinator_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fake_session = _make_fake_session()
    prepared = _make_prepared(fake_session)

    handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="test-ws")
    ctx = SimpleNamespace(
        session_id="sid-1",
        turn_id="turn-1",
        prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )
    await handler(ctx)

    assert fake_session.coordinator.config["workspace"] == "test-ws"


@pytest.mark.asyncio
async def test_runtime_writes_project_slug_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fake_session = _make_fake_session()
    prepared = _make_prepared(fake_session)

    handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="test-ws")
    ctx = SimpleNamespace(
        session_id="sid-1",
        turn_id="turn-1",
        prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )
    await handler(ctx)

    assert fake_session.coordinator.config["project_slug"] == "test-ws"


@pytest.mark.asyncio
async def test_runtime_uses_per_workspace_session_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    captured_roots: list[Path] = []

    real_store_cls = _runtime.SessionStore

    def _spy_store(root: Path):
        captured_roots.append(root)
        return real_store_cls(root)

    monkeypatch.setattr(_runtime, "SessionStore", _spy_store)

    fake_session = _make_fake_session()
    prepared = _make_prepared(fake_session)
    handler = _runtime.make_turn_handler(prepared, cwd=None, is_resumed=False, host_config=None, workspace="test-ws")
    ctx = SimpleNamespace(
        session_id="sid-1",
        turn_id="turn-1",
        prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )
    await handler(ctx)

    assert captured_roots, "SessionStore was never constructed"
    assert captured_roots[0] == state_root() / "workspaces" / "test-ws"
