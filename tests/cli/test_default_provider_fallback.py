"""Tests for D6 — bundle default_provider fallback in `run` command.

When `host.provider.module` is not set in the loaded host config, the CLI
must fall back to the bundle's `default_provider:` top-level field (rather
than env-var-based provider autodetection — removed in E5).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli

# All env vars that the legacy provider-detection (removed in E5) used to
# walk. Strip them all so the test cannot accidentally pass via env-var
# autodetection (which no longer exists in the CLI).
_PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_KEY",
    "OLLAMA_HOST",
)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_run_uses_bundle_default_provider_when_no_override(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No host.provider.module, no env vars → use bundle default_provider.

    The vendored bundle.md ships with `default_provider: anthropic` (D6).
    """
    # Strip every provider env var so any residual env-var-based fallback
    # would fail loudly. With E5 the CLI no longer reads these env vars
    # to pick a provider; only bundle.md default_provider remains.
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    captured: dict[str, Any] = {}

    async def _fake_execute_turn(spec):
        captured["provider"] = spec.provider
        return {"reply": "stub", "turnId": "turn-1"}

    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake_execute_turn):
        result = runner.invoke(cli, ["run", "-y", "hello"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert captured["provider"] == "anthropic"
