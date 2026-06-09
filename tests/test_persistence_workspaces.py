"""Tests for the workspace helpers in persistence.py.

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_agent_lib import persistence


def test_workspaces_root_under_state_root(monkeypatch, tmp_path: Path) -> None:
    """workspaces_root() == state_root() / 'workspaces', honouring XDG_STATE_HOME (D8)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert persistence.workspaces_root() == tmp_path / "amplifier-agent" / "workspaces"
    # And it is exactly state_root() / "workspaces".
    assert persistence.workspaces_root() == persistence.state_root() / "workspaces"


def test_validate_slug_accepts_valid() -> None:
    """A conforming slug is returned unchanged (D3)."""
    assert persistence.validate_slug("acme-api") == "acme-api"
    assert persistence.validate_slug("a") == "a"
    assert persistence.validate_slug("group-7f3a9d2c") == "group-7f3a9d2c"
    # Max length (64 chars) is accepted (D3 boundary).
    assert persistence.validate_slug("a" * 64) == "a" * 64


def test_validate_slug_rejects_uppercase() -> None:
    """Uppercase is not lowercase-normalized; it is rejected (D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("ACME")


def test_validate_slug_rejects_path_traversal() -> None:
    """Path-traversal is blocked at parse, before it can reach the filesystem (D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("../etc")
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("a/b")


def test_validate_slug_rejects_underscore_prefix() -> None:
    """Leading '_' is reserved for AAA-internal workspaces (D3, I7)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("_legacy")


def test_validate_slug_rejects_too_long() -> None:
    """64+ chars exceed the filesystem-safe bound (D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("a" * 65)


def test_validate_slug_rejects_empty() -> None:
    """Empty is rejected by validate_slug itself; tier fall-through is the caller's job (D2)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("")
