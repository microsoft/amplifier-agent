"""Tests for amplifier_agent_client.spawn: resolve_binary_path() and build_env().

TDD bullets (11b):
- resolve_binary_path returns AMPLIFIER_AGENT_BIN value when env var is set
- build_env drops unlisted variables
- build_env includes extras

Task 10 (A6 SC-3 — design §4.12.1):
- build_env raises AaaError on blocked PYTHONPATH key in extras
- build_env raises AaaError on blocked LD_PRELOAD key in extras
- build_env allows non-blocked custom keys in extras
"""

from __future__ import annotations

import pytest


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


def test_build_env_raises_on_blocked_pythonpath() -> None:
    """build_env raises AaaError(env_injection_rejected) when extra contains PYTHONPATH."""
    from amplifier_agent_client.session import AaaError
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    with pytest.raises(AaaError) as exc_info:
        build_env(
            process_env={"PATH": "/usr/bin"},
            allowlist=DEFAULT_ALLOWLIST,
            extra={"PYTHONPATH": "/evil/path"},
        )
    assert exc_info.value.code == "env_injection_rejected"
    assert exc_info.value.classification == "protocol"
    assert exc_info.value.severity == "error"


def test_build_env_raises_on_blocked_ld_preload() -> None:
    """build_env raises AaaError(env_injection_rejected) when extra contains LD_PRELOAD."""
    from amplifier_agent_client.session import AaaError
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    with pytest.raises(AaaError) as exc_info:
        build_env(
            process_env={"PATH": "/usr/bin"},
            allowlist=DEFAULT_ALLOWLIST,
            extra={"LD_PRELOAD": "/evil/lib.so"},
        )
    assert exc_info.value.code == "env_injection_rejected"
    assert exc_info.value.classification == "protocol"
    assert exc_info.value.severity == "error"


def test_build_env_allows_non_blocked_extras() -> None:
    """build_env permits arbitrary non-blocked keys (e.g. CUSTOM_SAFE_VAR) in extras."""
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    result = build_env(
        process_env={"PATH": "/usr/bin"},
        allowlist=DEFAULT_ALLOWLIST,
        extra={"CUSTOM_SAFE_VAR": "value"},
    )
    assert result["CUSTOM_SAFE_VAR"] == "value"


def test_probe_engine_version_is_async() -> None:
    """probe_engine_version must be a coroutine function (A6 SC-7, design §4.12.2).

    The wrapper's version probe must be async to avoid blocking the event loop
    while waiting on `amplifier-agent version --json`.
    """
    import asyncio

    from amplifier_agent_client.spawn import probe_engine_version

    assert asyncio.iscoroutinefunction(probe_engine_version), (
        "probe_engine_version must be async (SC-7). Change def to async def."
    )
