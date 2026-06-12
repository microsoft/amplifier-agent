"""Mode A integration test — drives `run` against the REAL Engine (not a MagicMock),
with a stubbed PreparedBundle so we don't hit any network or real provider.

Regression guard for the bug discovered during the 2026-05-19 cheatsheet
walk-through: single_turn.py called the fictional Engine.boot(provider=...)
classmethod and crashed with TypeError on first real invocation. The
pre-existing test suite missed it because every other Mode A test mocked Engine.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


class _StubPrepared:
    """Stand-in for amplifier_foundation PreparedBundle — duck-typed.

    NOTE: mount_plan is an instance attribute (NOT a ClassVar) so each test
    gets a fresh dict. The CLI's inject_provider() mutates mount_plan, and a
    shared dict across instances would mean one test's injection leaks into
    the next test's stub.
    """

    resolver: ClassVar[Any] = None
    bundle_package_paths: ClassVar[list] = []

    def __init__(self) -> None:
        self.mount_plan: dict[str, Any] = {"session": {}, "tools": []}
        #: Snapshots of self.mount_plan captured at each create_session() call.
        #: Tests that need to assert "what was in mount_plan when the engine
        #: actually used the bundle" inspect this list.
        self.create_session_mount_plan_snapshots: list[dict[str, Any]] = []

    async def create_session(self, **kwargs: Any) -> Any:
        # Snapshot the mount_plan AT create_session time — by this point the
        # CLI has had its chance to inject providers etc. The snapshot is a
        # shallow copy of the top-level keys, which is enough for assertions
        # since the values we care about (providers list) are themselves new
        # lists assigned to the key.
        self.create_session_mount_plan_snapshots.append(dict(self.mount_plan))
        session = MagicMock()
        session.execute = AsyncMock(return_value="stub-reply for prompt")
        return session


def test_mode_a_run_does_not_crash_with_typeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        return _StubPrepared()

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "-y", "ping"])

    assert "TypeError" not in (result.stderr or ""), f"TypeError leaked through:\n{result.stderr}"
    assert result.exit_code == 0, f"unexpected exit {result.exit_code}; stderr={result.stderr}"


def test_mode_a_run_emits_json_with_reply_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        return _StubPrepared()

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "-y", "say hi"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "reply" in parsed
    assert parsed["reply"] == "stub-reply for prompt"
    assert parsed["turnId"] == "turn-1"


def test_mode_a_run_injects_detected_provider_into_mount_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must mount the detected provider before create_session() runs.

    Captures the _StubPrepared instance via a closure, then asserts that the
    mount_plan snapshot taken at create_session() time contains a populated
    providers list with the env-resolved api_key. This is the regression guard
    for the "Error: No providers available" issue surfaced during the
    2026-05-19 cheatsheet walk-through.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-inject-test-12345")

    captured: list[_StubPrepared] = []

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        stub = _StubPrepared()
        captured.append(stub)
        return stub

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "-y", "ping"])

    assert result.exit_code == 0, f"unexpected exit {result.exit_code}; stderr={result.stderr}"
    assert captured, "load_and_prepare_cached was not called"

    prepared = captured[0]
    assert prepared.create_session_mount_plan_snapshots, "create_session() was never invoked"

    snapshot = prepared.create_session_mount_plan_snapshots[0]
    providers = snapshot.get("providers")
    assert providers is not None, "mount_plan['providers'] was not injected"
    assert isinstance(providers, list)
    assert len(providers) == 1
    entry = providers[0]
    assert entry["module"] == "provider-anthropic"
    assert entry["source"].startswith("git+https://")
    assert entry["config"]["api_key"] == "sk-ant-inject-test-12345"
    assert entry["config"]["priority"] == 1


def test_mode_a_host_config_provider_module_selects_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """host_config.provider.module selects the provider (no --provider flag).

    The --provider argv flag was removed; host_config.json is now the single
    source of truth for provider selection. This test pins that with the real
    Engine + a stubbed PreparedBundle, replacing the previous --provider
    override test of the same scenario.
    """
    # Set BOTH env vars so any latent env-var-based detection would prefer
    # anthropic (the bundle default). The host_config must override that.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    cfg = tmp_path / "host_config.json"
    cfg.write_text(json.dumps({"provider": {"module": "openai"}}), encoding="utf-8")

    captured: list[_StubPrepared] = []

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        stub = _StubPrepared()
        captured.append(stub)
        return stub

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--config", str(cfg), "-y", "ping"])

    assert result.exit_code == 0, f"unexpected exit {result.exit_code}; stderr={result.stderr}"
    snapshot = captured[0].create_session_mount_plan_snapshots[0]
    providers = snapshot["providers"]
    assert providers[0]["module"] == "provider-openai"
    assert providers[0]["config"]["api_key"] == "sk-openai-test"
