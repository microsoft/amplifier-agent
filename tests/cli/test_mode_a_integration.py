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
    """Stand-in for amplifier_foundation PreparedBundle — duck-typed."""

    mount_plan: ClassVar[dict] = {"session": {}, "tools": []}
    resolver: ClassVar[Any] = None
    bundle_package_paths: ClassVar[list] = []

    def create_session(self, **kwargs: Any) -> Any:
        session = MagicMock()
        session.execute = AsyncMock(return_value="stub-reply for prompt")
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx


def test_mode_a_run_does_not_crash_with_typeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        return _StubPrepared()

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "ping"])

    assert "TypeError" not in (result.stderr or ""), f"TypeError leaked through:\n{result.stderr}"
    assert result.exit_code == 0, f"unexpected exit {result.exit_code}; stderr={result.stderr}"


def test_mode_a_run_emits_json_with_reply_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        return _StubPrepared()

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "say hi"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "reply" in parsed
    assert parsed["reply"] == "stub-reply for prompt"
    assert parsed["turnId"] == "turn-1"
