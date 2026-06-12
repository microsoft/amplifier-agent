"""Tests for persistence.py — unified ~/.amplifier-agent/ filesystem paths."""

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
# 2. amplifier_agent_home
# ---------------------------------------------------------------------------


def test_amplifier_agent_home_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """amplifier_agent_home() uses $AMPLIFIER_AGENT_HOME when set."""
    override = str(tmp_path / "custom-home")
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", override)
    result = persistence.amplifier_agent_home()
    assert result == Path(override)


def test_amplifier_agent_home_default_is_dot_amplifier_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """amplifier_agent_home() defaults to ~/.amplifier-agent/ when env is unset."""
    monkeypatch.delenv("AMPLIFIER_AGENT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.amplifier_agent_home()
    assert result == tmp_path / ".amplifier-agent"


def test_amplifier_agent_home_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """amplifier_agent_home() expands ~ in $AMPLIFIER_AGENT_HOME."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", "~/aah-override")
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.amplifier_agent_home()
    assert result == tmp_path / "aah-override"


# ---------------------------------------------------------------------------
# 3. cache_root
# ---------------------------------------------------------------------------


def test_cache_root_under_amplifier_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cache_root() is <amplifier_agent_home>/cache/."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    result = persistence.cache_root()
    assert result == tmp_path / "cache"


def test_cache_root_default_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cache_root() defaults to ~/.amplifier-agent/cache/ when AMPLIFIER_AGENT_HOME is unset."""
    monkeypatch.delenv("AMPLIFIER_AGENT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.cache_root()
    assert result == tmp_path / ".amplifier-agent" / "cache"


# ---------------------------------------------------------------------------
# 4. config_root
# ---------------------------------------------------------------------------


def test_config_root_under_amplifier_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config_root() is <amplifier_agent_home>/config/."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    result = persistence.config_root()
    assert result == tmp_path / "config"


def test_config_root_default_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config_root() defaults to ~/.amplifier-agent/config/ when AMPLIFIER_AGENT_HOME is unset."""
    monkeypatch.delenv("AMPLIFIER_AGENT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.config_root()
    assert result == tmp_path / ".amplifier-agent" / "config"


# ---------------------------------------------------------------------------
# 5. state_root
# ---------------------------------------------------------------------------


def test_state_root_under_amplifier_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state_root() is <amplifier_agent_home>/state/."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    result = persistence.state_root()
    assert result == tmp_path / "state"


def test_state_root_default_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state_root() defaults to ~/.amplifier-agent/state/ when AMPLIFIER_AGENT_HOME is unset."""
    monkeypatch.delenv("AMPLIFIER_AGENT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = persistence.state_root()
    assert result == tmp_path / ".amplifier-agent" / "state"


# ---------------------------------------------------------------------------
# 6. All three sub-dirs share the same AMPLIFIER_AGENT_HOME root
# ---------------------------------------------------------------------------


def test_all_roots_share_amplifier_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cache_root, config_root, state_root all resolve under the same AMPLIFIER_AGENT_HOME."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    assert persistence.cache_root() == tmp_path / "cache"
    assert persistence.config_root() == tmp_path / "config"
    assert persistence.state_root() == tmp_path / "state"


# ---------------------------------------------------------------------------
# 7. prepared_bundle_dir
# ---------------------------------------------------------------------------


def test_prepared_bundle_dir_includes_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """prepared_bundle_dir() uses __version__ and has correct structure."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    result = persistence.prepared_bundle_dir()
    assert result == tmp_path / "cache" / "prepared" / __version__


def test_prepared_bundle_dir_explicit_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """prepared_bundle_dir(version='9.9.9') uses the explicit version."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    result = persistence.prepared_bundle_dir(version="9.9.9")
    assert result == tmp_path / "cache" / "prepared" / "9.9.9"


# ---------------------------------------------------------------------------
# 8. session_state_dir
# ---------------------------------------------------------------------------


def test_session_state_dir_correct_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """session_state_dir('abc123') returns state_root()/sessions/abc123."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    result = persistence.session_state_dir("abc123")
    assert result == tmp_path / "state" / "sessions" / "abc123"


def test_session_state_dir_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """session_state_dir raises ValueError for '', '../etc', 'a/b', and backslash ids."""
    invalid_ids = ["", "../etc", "a/b", "foo\\bar", ".."]
    for bad_id in invalid_ids:
        with pytest.raises(ValueError, match="session_id"):
            persistence.session_state_dir(bad_id)
