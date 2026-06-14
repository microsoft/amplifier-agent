"""Unit tests for binary discovery and env allowlist (no subprocess spawn)."""

from __future__ import annotations

import pytest

from amplifier_agent_py import (
    BLOCKED_ENV_KEYS,
    DEFAULT_ALLOWLIST,
    AaaError,
    build_env,
    resolve_binary_path,
)


def test_resolve_binary_path_returns_env_var_value() -> None:
    path = resolve_binary_path({"AMPLIFIER_AGENT_BIN": "/usr/local/bin/amplifier-agent"})
    assert path == "/usr/local/bin/amplifier-agent"


def test_resolve_binary_path_raises_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(AaaError) as exc_info:
        resolve_binary_path({})
    assert exc_info.value.code == "binary_not_found"


def test_build_env_includes_default_allowlist_keys() -> None:
    process_env = {"PATH": "/usr/bin", "HOME": "/home/u", "SECRET": "nope"}
    result = build_env(process_env=process_env, allowlist=DEFAULT_ALLOWLIST)
    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/u"
    assert "SECRET" not in result


def test_build_env_includes_amplifier_prefix() -> None:
    process_env = {"AMPLIFIER_LOG_LEVEL": "debug", "RANDOM": "x"}
    result = build_env(process_env=process_env, allowlist=[])
    assert result["AMPLIFIER_LOG_LEVEL"] == "debug"
    assert "RANDOM" not in result


def test_build_env_includes_lc_prefix() -> None:
    process_env = {"LC_ALL": "C.UTF-8", "RANDOM": "x"}
    result = build_env(process_env=process_env, allowlist=[])
    assert result["LC_ALL"] == "C.UTF-8"


def test_build_env_extra_overrides_allowlisted_value() -> None:
    process_env = {"HOME": "/home/u"}
    result = build_env(
        process_env=process_env,
        allowlist=["HOME"],
        extra={"HOME": "/sandbox"},
    )
    assert result["HOME"] == "/sandbox"


def test_build_env_rejects_blocked_keys_in_extra() -> None:
    for key in BLOCKED_ENV_KEYS:
        with pytest.raises(AaaError) as exc_info:
            build_env(process_env={}, allowlist=[], extra={key: "evil"})
        assert exc_info.value.code == "env_injection_rejected"
        assert exc_info.value.classification == "protocol"


def test_build_env_pythonpath_rejected_even_when_allowlisted() -> None:
    # Even if a user puts PYTHONPATH in the allowlist, the blocklist on
    # `extra` ensures host-supplied PYTHONPATH cannot reach the subprocess.
    with pytest.raises(AaaError):
        build_env(
            process_env={"PYTHONPATH": "/usr/local"},
            allowlist=["PYTHONPATH"],
            extra={"PYTHONPATH": "/attacker"},
        )
