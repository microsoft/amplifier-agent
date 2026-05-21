"""Tests for amplifier_agent_client.spawn: resolve_binary_path() and build_env().

TDD bullets (11b):
- resolve_binary_path returns AMPLIFIER_AGENT_BIN value when env var is set
- build_env drops unlisted variables
- build_env includes extras
"""

from __future__ import annotations


def test_resolve_binary_env_override_returns_bin_sh() -> None:
    """resolve_binary_path returns AMPLIFIER_AGENT_BIN value when set (uses /bin/sh as guaranteed path)."""
    from amplifier_agent_client.spawn import resolve_binary_path

    result = resolve_binary_path(env={"AMPLIFIER_AGENT_BIN": "/bin/sh"})
    assert result == "/bin/sh"


def test_build_env_drops_unlisted_produces_only_path_home() -> None:
    """build_env drops variables not in allowlist, keeping only PATH and HOME."""
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    process_env = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "SECRET_TOKEN": "should-be-dropped",
    }
    result = build_env(process_env=process_env, allowlist=DEFAULT_ALLOWLIST)
    assert "PATH" in result
    assert "HOME" in result
    assert "SECRET_TOKEN" not in result


def test_build_env_extras_win_includes_custom() -> None:
    """build_env merges extras, making CUSTOM key appear in result."""
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    process_env = {
        "PATH": "/usr/bin",
    }
    result = build_env(process_env=process_env, allowlist=DEFAULT_ALLOWLIST, extra={"CUSTOM": "value"})
    assert result["CUSTOM"] == "value"
