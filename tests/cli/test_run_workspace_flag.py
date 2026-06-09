"""The `run --workspace <slug>` argv surface (D1).

Spec: the flag parses cleanly when present and threads onto _TurnSpec; an
invalid slug surfaces a clean error envelope rather than a traceback.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


def test_workspace_flag_accepted() -> None:
    """`run --workspace foo` parses and reaches _execute_turn with workspace='foo'."""
    captured: dict[str, object] = {}

    async def _fake_exec(spec):
        captured["workspace"] = spec.workspace
        return {"reply": "ok", "turnId": "turn-1", "sessionId": ""}

    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake_exec):
        result = runner.invoke(
            cli,
            ["run", "--workspace", "foo", "hello"],
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    assert result.exit_code == 0, result.output
    assert captured.get("workspace") == "foo"


def test_workspace_flag_invalid_format_errors_cleanly() -> None:
    """--workspace with an invalid slug reaches _execute_turn without raising at the CLI layer.

    Note: B1 is a pure pass-through — no slug validation yet. This test confirms
    the flag parses and the raw value is threaded through unchanged. B4 will
    add fail-fast WorkspaceError behavior with its own dedicated test.
    """
    captured: dict[str, object] = {}

    async def _fake_exec(spec):
        captured["workspace"] = spec.workspace
        return {"reply": "ok", "turnId": "turn-1", "sessionId": ""}

    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake_exec):
        result = runner.invoke(
            cli,
            ["run", "--workspace", "Bad Slug", "hello"],
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    # B1 contract: raw string passes through unchanged. Exit is clean because
    # _execute_turn is patched (no real engine call). No traceback, no validation.
    assert result.exit_code == 0, result.output
    assert captured.get("workspace") == "Bad Slug"
    assert "Traceback" not in result.output
