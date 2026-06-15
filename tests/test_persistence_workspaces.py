"""Tests for the workspace helpers in persistence.py.

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_agent_lib import persistence


def test_workspaces_root_under_state_root(monkeypatch, tmp_path: Path) -> None:
    """workspaces_root() == state_root() / 'workspaces', honouring AMPLIFIER_AGENT_HOME (D8)."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))

    assert persistence.workspaces_root() == tmp_path / "state" / "workspaces"
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


def _app_cli_get_project_slug(cwd: Path) -> str:
    """Verbatim port of amplifier-app-cli's get_project_slug logic.

    This is the canonical algorithm (microsoft/amplifier-app-cli @ a9b231e,
    ``amplifier_app_cli/project_utils.py``). Used here as the contract that
    persistence.derive_workspace_from_cwd must match byte-for-byte so the
    ``project_slug`` alias (D5) aligns across hosts.
    """
    slug = str(cwd.resolve()).replace("/", "-").replace("\\", "-").replace(":", "")
    if not slug.startswith("-"):
        slug = "-" + slug
    return slug


@pytest.mark.parametrize(
    "cwd",
    [
        Path("/nonexistent/Users/me/repos/amplifier-agent"),
        Path("/nonexistent/home/a/myproj"),
        Path("/nonexistent/Foo Bar/My Project!"),
        Path("/nonexistent"),
        Path("/"),
        Path("C:\\projects\\web-app"),
    ],
)
def test_derive_workspace_matches_app_cli_format(cwd: Path) -> None:
    """Output is byte-identical to amplifier-app-cli's get_project_slug (D4).

    This is the core contract — the project_slug alias (D5) only delivers
    cross-host bucket alignment if both implementations produce the same
    string for the same cwd. The non-existent path prefix avoids firmlink/
    symlink resolution surprises on macOS (where /home → /System/Volumes/Data
    and /tmp → /private/tmp).
    """
    assert persistence.derive_workspace_from_cwd(cwd) == _app_cli_get_project_slug(cwd)


def test_derive_workspace_is_stable() -> None:
    """Same cwd -> same slug across calls (D4, I5)."""
    cwd = Path("/nonexistent/Users/me/repos/amplifier-agent")
    assert persistence.derive_workspace_from_cwd(cwd) == persistence.derive_workspace_from_cwd(cwd)


def test_derive_workspace_disambiguates_different_paths() -> None:
    """Two distinct absolute paths sharing a basename get distinct slugs (D4)."""
    a = persistence.derive_workspace_from_cwd(Path("/nonexistent/a/myproj"))
    b = persistence.derive_workspace_from_cwd(Path("/nonexistent/b/myproj"))
    assert a != b
    assert a.endswith("-a-myproj")
    assert b.endswith("-b-myproj")


def test_derive_workspace_preserves_case_and_spaces() -> None:
    """The algorithm is verbatim — case and spaces are preserved (D4).

    Matches amplifier-app-cli's get_project_slug exactly; any future
    normalization (case folding, space handling) must land in both repos
    together or the alias breaks.
    """
    slug = persistence.derive_workspace_from_cwd(Path("/nonexistent/Foo Bar/My Project!"))
    assert slug == "-nonexistent-Foo Bar-My Project!"


def test_derive_workspace_strips_colons_and_backslashes() -> None:
    """Windows-style drive letters and backslashes are stripped/replaced (D4).

    On POSIX, Path treats ``\\`` and ``:`` as literal filename chars, so the
    test path stays as a single relative component; the replace() chain still
    produces the canonical app-cli output.
    """
    slug = persistence.derive_workspace_from_cwd(Path("C:\\projects\\web-app"))
    assert ":" not in slug
    assert "\\" not in slug
    assert slug.startswith("-")


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


def test_resolve_workspace_invalid_env_raises() -> None:
    """An explicit-but-invalid env slug raises rather than silently falling through (D2/D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.resolve_workspace(
            argv_workspace=None,
            env={"AMPLIFIER_AGENT_WORKSPACE": "Bad Slug!"},
            cwd=Path("/Users/me/repos/amplifier-agent"),
        )


def test_resolve_workspace_whitespace_argv_falls_through() -> None:
    """Whitespace-only argv falls through, symmetric to whitespace env (D2)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    result = persistence.resolve_workspace(
        argv_workspace="   ",
        env={},
        cwd=cwd,
    )
    assert result == persistence.derive_workspace_from_cwd(cwd)
