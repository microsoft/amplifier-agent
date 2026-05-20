"""Tests for Gap (c) — threading CLI flags (--session-id / --resume / --provider / --cwd)
through Engine.boot() init_params in single_turn._execute_turn.

2 tests covering:
  1. test_run_passes_cwd_to_make_turn_handler
  2. test_run_passes_provider_override_to_detect_provider
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_engine(captured: dict[str, Any]) -> type:
    """Return a fake Engine class that captures init_params in *captured*.

    The class satisfies the duck-type contract that single_turn._execute_turn
    expects: __init__, boot (async), submit_turn (async), shutdown (async).
    """

    class _FakeEngine:
        def __init__(self, *, turn_handler: Any, protocol_points: Any) -> None:
            self._turn_handler = turn_handler

        async def boot(self, init_params: Any, bundle_override: Any = None) -> Any:
            # Capture a copy so later mutations don't affect assertions.
            captured["init_params"] = dict(init_params)
            return {
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "0"},
                "sessionState": {
                    "sessionId": init_params.get("sessionId", ""),
                    "resumed": init_params.get("resume", False),
                },
            }

        async def submit_turn(self, params: Any) -> Any:
            return {"reply": "ok", "turnId": params.get("turnId", "turn-1")}

        async def shutdown(self, _params: Any = None) -> Any:
            return {}

    return _FakeEngine


async def _fake_load(*, aaa_version: str) -> Any:
    """Return a MagicMock in place of the real PreparedBundle."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Test 1: cwd reaches make_turn_handler AND Engine.boot init_params
# ---------------------------------------------------------------------------


def test_run_passes_cwd_to_make_turn_handler() -> None:
    """--cwd /tmp reaches make_turn_handler (cwd='/tmp', is_resumed=True) AND
    is included in init_params sent to Engine.boot (gap c).

    Part one (make_turn_handler assertions) tests existing plumbing.
    Part two (init_params assertion) is the TDD RED gate that fails until
    single_turn._execute_turn adds cwd to the init_params dict.
    """
    captured: dict[str, Any] = {}

    def _fake_make_turn_handler(prepared: Any, *, cwd: Any, is_resumed: Any) -> Any:
        captured["cwd"] = cwd
        captured["is_resumed"] = is_resumed

        async def _noop(ctx: Any) -> str:
            return "noop"

        return _noop

    FakeEngine = _make_fake_engine(captured)

    runner = CliRunner()
    with (
        patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _fake_load),
        patch("amplifier_agent_cli.provider_sources.inject_provider"),
        patch("amplifier_agent_cli.modes.single_turn.detect_provider", return_value="anthropic"),
        patch("amplifier_agent_cli.modes.single_turn.Engine", FakeEngine),
        patch("amplifier_agent_cli.modes.single_turn.make_turn_handler", _fake_make_turn_handler),
    ):
        result = runner.invoke(
            cli,
            ["run", "hello", "--cwd", "/tmp", "--session-id", "sess-xyz", "--resume"],
        )

    assert result.exit_code == 0, f"Expected exit 0. Output: {result.output}"

    # --- existing plumbing (already works) ---
    assert captured.get("cwd") == "/tmp", f"make_turn_handler should receive cwd='/tmp', got {captured.get('cwd')!r}"
    assert captured.get("is_resumed") is True, (
        f"make_turn_handler should receive is_resumed=True, got {captured.get('is_resumed')!r}"
    )

    # --- gap (c): init_params must include cwd ---
    assert "cwd" in captured.get("init_params", {}), (
        "Engine.boot init_params must include 'cwd' when --cwd is passed. "
        f"Got init_params keys: {list(captured.get('init_params', {}).keys())}"
    )
    assert captured["init_params"]["cwd"] == "/tmp", (
        f"Expected init_params['cwd']='/tmp', got {captured['init_params'].get('cwd')!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: --provider override reaches detect_provider AND Engine.boot init_params
# ---------------------------------------------------------------------------


def test_run_passes_provider_override_to_detect_provider() -> None:
    """--provider anthropic reaches detect_provider (override='anthropic') AND
    is included in init_params sent to Engine.boot as 'providerOverride' (gap c).

    Part one (detect_provider assertion) tests existing plumbing.
    Part two (init_params assertion) is the TDD RED gate that fails until
    single_turn._execute_turn adds providerOverride to the init_params dict.
    """
    captured: dict[str, Any] = {}

    def _fake_detect_provider(override: str | None = None) -> str:
        captured["override"] = override
        return "anthropic"

    def _fake_make_turn_handler(prepared: Any, *, cwd: Any, is_resumed: Any) -> Any:
        async def _noop(ctx: Any) -> str:
            return "noop"

        return _noop

    FakeEngine = _make_fake_engine(captured)

    runner = CliRunner()
    with (
        patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _fake_load),
        patch("amplifier_agent_cli.provider_sources.inject_provider"),
        patch("amplifier_agent_cli.modes.single_turn.detect_provider", _fake_detect_provider),
        patch("amplifier_agent_cli.modes.single_turn.Engine", FakeEngine),
        patch("amplifier_agent_cli.modes.single_turn.make_turn_handler", _fake_make_turn_handler),
    ):
        result = runner.invoke(cli, ["run", "hello", "--provider", "anthropic"])

    assert result.exit_code == 0, f"Expected exit 0. Output: {result.output}"

    # --- existing plumbing (already works) ---
    assert captured.get("override") == "anthropic", (
        f"detect_provider should receive override='anthropic', got {captured.get('override')!r}"
    )

    # --- gap (c): init_params must include providerOverride ---
    assert "providerOverride" in captured.get("init_params", {}), (
        "Engine.boot init_params must include 'providerOverride' when --provider is passed. "
        f"Got init_params keys: {list(captured.get('init_params', {}).keys())}"
    )
    assert captured["init_params"]["providerOverride"] == "anthropic", (
        f"Expected init_params['providerOverride']='anthropic', got {captured['init_params'].get('providerOverride')!r}"
    )
