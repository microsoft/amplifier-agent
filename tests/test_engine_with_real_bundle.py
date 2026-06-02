"""Tests for Engine.boot() with real bundle cache and bundle_override.

Two tests:
1. test_engine_boots_with_real_bundle  — no bundle_override → hits load_and_prepare_cached
2. test_engine_accepts_bundle_override_for_tests — stub injected → cache is NOT called
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import ClassVar

import pytest

from amplifier_agent_lib.engine import Engine, TurnContext
from amplifier_agent_lib.protocol_points import (
    CliApprovalSystem,
    CliDisplaySystem,
    DisplayVerbosity,
    ProtocolPoints,
)

# ---------------------------------------------------------------------------
# Minimal helpers
# ---------------------------------------------------------------------------


async def _noop_handler(ctx: TurnContext) -> str:
    """No-op turn handler — returns a canned reply without touching the model."""
    return "noop"


def _make_engine() -> Engine:
    """Create a minimal Engine wired to an in-memory StringIO display."""
    buf = io.StringIO()
    display = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.VERBOSE)
    approval = CliApprovalSystem(override=None, is_tty=False)
    protocol_points: ProtocolPoints = {"approval": approval, "display": display}
    return Engine(turn_handler=_noop_handler, protocol_points=protocol_points)


def _boot_params(**kwargs: object) -> dict:
    """Return a minimal InitializeParams dict."""
    params: dict = {
        "protocolVersion": "0.2.0",
        "clientInfo": {"name": "test-client", "version": "0.0.0"},
        "capabilities": {
            "display": {"events": []},
            "approval": {"actions": ["accept", "decline", "cancel"]},
        },
        "sessionId": "test-session",
    }
    params.update(kwargs)
    return params


# ---------------------------------------------------------------------------
# Test 1: real bundle (cold + cache path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_boots_with_real_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine.boot() with no bundle_override calls load_and_prepare_cached and sets engine.session."""
    # Redirect XDG cache to tmp_path so we don't pollute the real cache and
    # the cold-path write is contained inside the test's temp directory.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    engine = _make_engine()
    try:
        await engine.boot(_boot_params(sessionId="test-real-bundle"))
        # After boot, engine.session must be populated (the PreparedBundle from cache).
        assert engine.session is not None
    finally:
        await engine.shutdown()


# ---------------------------------------------------------------------------
# Test 2: bundle_override for tests (stub path — cache must NOT be called)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_accepts_bundle_override_for_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine.boot() with bundle_override=StubPrepared() uses the stub and ignores the cache."""

    class StubPrepared:
        """Minimal stand-in for a PreparedBundle — carries the same duck-typed attributes."""

        mount_plan: ClassVar[dict] = {"session": {}, "tools": []}
        resolver: ClassVar = None

    cache_called: list[str] = []

    async def _mock_load_and_prepare_cached(aaa_version: str) -> StubPrepared:  # type: ignore[return]
        cache_called.append(aaa_version)
        raise AssertionError("load_and_prepare_cached must not be called when bundle_override is provided")

    # Patch the name as imported inside engine.py.
    monkeypatch.setattr(
        "amplifier_agent_lib.engine.load_and_prepare_cached",
        _mock_load_and_prepare_cached,
    )

    engine = _make_engine()
    stub = StubPrepared()
    try:
        await engine.boot(_boot_params(sessionId="test-stub"), bundle_override=stub)  # type: ignore[arg-type]
        # The stub must be stored as engine.session.
        assert engine.session is not None
        assert engine.session is stub, "engine.session must be the injected stub, not a freshly cached bundle"
        # The cache function must never have been reached.
        assert not cache_called, f"load_and_prepare_cached was called unexpectedly: {cache_called}"
    finally:
        await engine.shutdown()
