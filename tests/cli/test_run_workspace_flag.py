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
    """--workspace with an invalid slug exits 2 with a clean §4.1 error envelope (B4 fail-fast).

    B4 added workspace validation at the CLI layer via resolve_workspace() before
    _execute_turn is called. An invalid slug now yields exit code 2 and a structured
    error envelope — no traceback, no unhandled exception.
    """
    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._execute_turn"):
        result = runner.invoke(
            cli,
            ["run", "--workspace", "Bad Slug", "hello"],
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    # B4 contract: invalid slug is rejected before engine boot with exit code 2.
    assert result.exit_code == 2, result.output
    assert "argv_workspace_invalid" in result.output
    assert "Traceback" not in result.output
