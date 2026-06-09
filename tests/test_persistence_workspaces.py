"""Tests for the workspace resolution helpers in persistence.py.

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (D1-D4).
"""

from __future__ import annotations

from pathlib import Path

from amplifier_agent_lib import persistence


def test_workspaces_root_under_state_root(monkeypatch, tmp_path: Path) -> None:
    """workspaces_root() == state_root() / 'workspaces', honouring XDG_STATE_HOME (D8)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert persistence.workspaces_root() == tmp_path / "amplifier-agent" / "workspaces"
    # And it is exactly state_root() / "workspaces".
    assert persistence.workspaces_root() == persistence.state_root() / "workspaces"
