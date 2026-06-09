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


def test_derive_workspace_is_stable() -> None:
    """Same cwd -> same slug across calls (D4, I5)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    first = persistence.derive_workspace_from_cwd(cwd)
    second = persistence.derive_workspace_from_cwd(cwd)
    assert first == second
    # The derived slug must itself be valid (constructed-valid invariant, D4).
    assert persistence.validate_slug(first) == first


def test_derive_workspace_disambiguates_same_basename() -> None:
    """Two absolute paths sharing a basename get different slugs (D4 hash suffix)."""
    a = persistence.derive_workspace_from_cwd(Path("/home/a/myproj"))
    b = persistence.derive_workspace_from_cwd(Path("/home/b/myproj"))
    assert a != b
    assert a.startswith("myproj-")
    assert b.startswith("myproj-")


def test_derive_workspace_handles_root() -> None:
    """'/' has an empty basename; falls back to 'default-<hash>' (D4)."""
    slug = persistence.derive_workspace_from_cwd(Path("/"))
    assert slug.startswith("default-")
    assert persistence.validate_slug(slug) == slug


def test_derive_workspace_handles_invalid_basename() -> None:
    """A basename with spaces/punctuation slugifies cleanly (D4)."""
    slug = persistence.derive_workspace_from_cwd(Path("/tmp/My Project!"))
    assert slug.startswith("my-project-")
    assert persistence.validate_slug(slug) == slug


def test_resolve_workspace_argv_wins() -> None:
    """argv flag beats env and cwd (D2, first-hit-wins)."""
    result = persistence.resolve_workspace(
        argv_workspace="from-flag",
        env={"AMPLIFIER_AGENT_WORKSPACE": "from-env"},
        cwd=Path("/Users/me/repos/amplifier-agent"),
    )
    assert result == "from-flag"


def test_resolve_workspace_env_when_no_argv() -> None:
    """env is used when argv is absent (D2)."""
    result = persistence.resolve_workspace(
        argv_workspace=None,
        env={"AMPLIFIER_AGENT_WORKSPACE": "from-env"},
        cwd=Path("/Users/me/repos/amplifier-agent"),
    )
    assert result == "from-env"


def test_resolve_workspace_cwd_fallback() -> None:
    """With neither argv nor env, fall back to the cwd-derived slug (D2/D4)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    result = persistence.resolve_workspace(argv_workspace=None, env={}, cwd=cwd)
    assert result == persistence.derive_workspace_from_cwd(cwd)


def test_resolve_workspace_empty_argv_falls_through() -> None:
    """Empty argv string falls through to env, then cwd (D2)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    # Empty argv + empty/whitespace env -> cwd-derived.
    result = persistence.resolve_workspace(
        argv_workspace="",
        env={"AMPLIFIER_AGENT_WORKSPACE": "   "},
        cwd=cwd,
    )
    assert result == persistence.derive_workspace_from_cwd(cwd)


def test_resolve_workspace_invalid_argv_raises() -> None:
    """An explicit-but-invalid argv slug raises rather than silently falling through (D2/D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.resolve_workspace(
            argv_workspace="Bad Slug",
            env={},
            cwd=Path("/Users/me/repos/amplifier-agent"),
        )
