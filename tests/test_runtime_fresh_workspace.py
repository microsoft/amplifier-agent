"""`--fresh` cleans only the per-workspace session dir (I8).

We exercise _execute_turn's cleanup branch in isolation by stubbing the
post-cleanup engine work, so the test stays fast and free of real LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_cli.modes import single_turn
from amplifier_agent_lib.persistence import state_root


def _seed_session(workspace: str, session_id: str, monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sess = state_root() / "workspaces" / workspace / "sessions" / session_id
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "transcript.jsonl").write_text('{"role":"user"}', encoding="utf-8")
    return sess


def _make_spec(workspace: str, session_id: str):
    spec = MagicMock()
    spec.workspace = workspace
    spec.session_id = session_id
    spec.fresh = True
    spec.resume = False
    spec.cwd = None
    spec.provider = "anthropic"
    spec.host_config = None
    spec.allow_protocol_skew = False
    spec.prompt = "hi"
    return spec


def _stub_engine_path(monkeypatch) -> None:
    """Stub everything after the --fresh cleanup so _execute_turn returns fast."""
    monkeypatch.setattr(single_turn, "load_and_prepare_cached", AsyncMock(return_value=MagicMock()))
    # inject_provider is imported locally inside _execute_turn (not at module level),
    # so patch it at the source module rather than the single_turn namespace.
    import amplifier_agent_cli.provider_sources as _ps

    monkeypatch.setattr(_ps, "inject_provider", lambda *a, **k: None)
    monkeypatch.setattr(single_turn, "make_turn_handler", lambda *a, **k: None)
    fake_engine = MagicMock()
    fake_engine.boot = AsyncMock()
    fake_engine.submit_turn = AsyncMock(return_value={"reply": "ok", "turnId": "turn-1"})
    fake_engine.shutdown = AsyncMock()
    monkeypatch.setattr(single_turn, "Engine", lambda *a, **k: fake_engine)


@pytest.mark.asyncio
async def test_fresh_cleans_workspace_scoped_session_dir(monkeypatch, tmp_path) -> None:
    sess = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    assert (sess / "transcript.jsonl").exists()
    _stub_engine_path(monkeypatch)

    await single_turn._execute_turn(_make_spec("ws-a", "sid-1"))

    assert not sess.exists(), "the per-workspace session dir should have been removed"


@pytest.mark.asyncio
async def test_fresh_leaves_other_workspaces_untouched(monkeypatch, tmp_path) -> None:
    sess_a = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    sess_b = _seed_session("ws-b", "sid-1", monkeypatch, tmp_path)
    _stub_engine_path(monkeypatch)

    await single_turn._execute_turn(_make_spec("ws-a", "sid-1"))

    assert not sess_a.exists()
    assert sess_b.exists(), "--fresh must not touch a different workspace"


@pytest.mark.asyncio
async def test_fresh_with_no_existing_session_no_op(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _stub_engine_path(monkeypatch)

    # No session seeded; cleanup must be a silent no-op (no error).
    result = await single_turn._execute_turn(_make_spec("ws-a", "missing"))
    assert result["reply"] == "ok"
