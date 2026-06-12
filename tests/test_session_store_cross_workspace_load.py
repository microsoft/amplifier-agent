"""Cross-workspace resume fallback for SessionStore.load (D10)."""

from __future__ import annotations

import logging
from pathlib import Path

from amplifier_agent_lib.session_store import SessionStore


def _workspaces_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    return tmp_path / "state" / "workspaces"


def test_load_finds_in_current_workspace(tmp_path, monkeypatch) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    store = SessionStore(ws_root / "current")
    store.save("sid-1", [{"role": "user", "content": "hi"}], {"k": "v"})

    result = store.load("sid-1")
    assert result is not None
    transcript, _ = result
    assert transcript == [{"role": "user", "content": "hi"}]


def test_load_walks_workspaces_when_not_in_current(tmp_path, monkeypatch) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    # Session lives in "other", but we load from "current".
    other = SessionStore(ws_root / "other")
    other.save("sid-2", [{"role": "user", "content": "elsewhere"}], {})

    current = SessionStore(ws_root / "current")
    result = current.load("sid-2")

    assert result is not None
    transcript, _ = result
    assert transcript == [{"role": "user", "content": "elsewhere"}]


def test_load_logs_when_found_in_different_workspace(tmp_path, monkeypatch, caplog) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    SessionStore(ws_root / "_legacy").save("sid-3", [{"role": "user"}], {})
    current = SessionStore(ws_root / "current")

    with caplog.at_level(logging.INFO):
        current.load("sid-3")

    assert any(
        "found sid-3 in workspace _legacy" in r.message and "current=current" in r.message for r in caplog.records
    )


def test_load_returns_none_when_nowhere(tmp_path, monkeypatch) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    store = SessionStore(ws_root / "current")
    assert store.load("does-not-exist") is None
