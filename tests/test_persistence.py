"""Tests for persistence.py — XDG-compliant filesystem paths."""

from __future__ import annotations

from pathlib import Path

import pytest

import amplifier_agent_lib.persistence as persistence
from amplifier_agent_lib import __version__
from amplifier_agent_lib.persistence import APP_NAME

# ---------------------------------------------------------------------------
# 1. APP_NAME constant
# ---------------------------------------------------------------------------


def test_app_name_constant() -> None:
    """APP_NAME is the string 'amplifier-agent'."""
    assert APP_NAME == "amplifier-agent"


# ---------------------------------------------------------------------------
# 2. cache_root
# ---------------------------------------------------------------------------


def test_cache_root_uses_xdg_cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cache_root() uses $XDG_CACHE_HOME when set."""
    xdg_cache = str(tmp_path / "xdg_cache")
    monkeypatch.setenv("XDG_CACHE_HOME", xdg_cache)
    result = persistence.cache_root()
    assert result.parts[-1] == APP_NAME
    assert str(result).startswith(xdg_cache)


def test_cache_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cache_root() falls back to ~/.cache/<APP_NAME> when XDG_CACHE_HOME is unset."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.cache_root()
    assert result == tmp_path / ".cache" / APP_NAME


# ---------------------------------------------------------------------------
# 3. config_root
# ---------------------------------------------------------------------------


def test_config_root_uses_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config_root() uses $XDG_CONFIG_HOME when set."""
    xdg_config = str(tmp_path / "xdg_config")
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg_config)
    result = persistence.config_root()
    assert result.parts[-1] == APP_NAME
    assert str(result).startswith(xdg_config)


def test_config_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config_root() falls back to ~/.config/<APP_NAME> when XDG_CONFIG_HOME is unset."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.config_root()
    assert result == tmp_path / ".config" / APP_NAME


# ---------------------------------------------------------------------------
# 4. state_root
# ---------------------------------------------------------------------------


def test_state_root_uses_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state_root() uses $XDG_STATE_HOME when set."""
    xdg_state = str(tmp_path / "xdg_state")
    monkeypatch.setenv("XDG_STATE_HOME", xdg_state)
    result = persistence.state_root()
    assert result.parts[-1] == APP_NAME
    assert str(result).startswith(xdg_state)


def test_state_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state_root() falls back to ~/.local/state/<APP_NAME> when XDG_STATE_HOME is unset."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.state_root()
    assert result == tmp_path / ".local" / "state" / APP_NAME


# ---------------------------------------------------------------------------
# 4b. Empty XDG env vars treated as absent (D9)
# ---------------------------------------------------------------------------


def test_cache_root_treats_empty_xdg_cache_home_as_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cache_root() treats empty XDG_CACHE_HOME as if unset, falling back to ~/.cache/<APP_NAME>."""
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert persistence.cache_root() == tmp_path / ".cache" / APP_NAME


def test_config_root_treats_empty_xdg_config_home_as_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config_root() treats empty XDG_CONFIG_HOME as if unset, falling back to ~/.config/<APP_NAME>."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert persistence.config_root() == tmp_path / ".config" / APP_NAME


def test_state_root_treats_empty_xdg_state_home_as_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state_root() treats empty XDG_STATE_HOME as if unset, falling back to ~/.local/state/<APP_NAME>."""
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert persistence.state_root() == tmp_path / ".local" / "state" / APP_NAME


# ---------------------------------------------------------------------------
# 5. prepared_bundle_dir
# ---------------------------------------------------------------------------


def test_prepared_bundle_dir_includes_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """prepared_bundle_dir() uses __version__ and has correct structure."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.prepared_bundle_dir()
    parts = result.parts
    assert parts[-3] == APP_NAME
    assert parts[-2] == "prepared"
    assert parts[-1] == __version__


def test_prepared_bundle_dir_explicit_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """prepared_bundle_dir(version='9.9.9') uses the explicit version."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.prepared_bundle_dir(version="9.9.9")
    parts = result.parts
    assert parts[-3] == APP_NAME
    assert parts[-2] == "prepared"
    assert parts[-1] == "9.9.9"


# ---------------------------------------------------------------------------
# 6. session_state_dir
# ---------------------------------------------------------------------------


def test_session_state_dir_correct_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """session_state_dir('abc123') returns state_root()/sessions/abc123."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.session_state_dir("abc123")
    assert result == tmp_path / ".local" / "state" / APP_NAME / "sessions" / "abc123"


def test_session_state_dir_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """session_state_dir raises ValueError for '', '../etc', 'a/b', and backslash ids."""
    invalid_ids = ["", "../etc", "a/b", "foo\\bar", ".."]
    for bad_id in invalid_ids:
        with pytest.raises(ValueError, match="session_id"):
            persistence.session_state_dir(bad_id)
