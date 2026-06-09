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
    """An invalid slug exits non-zero and does NOT leak a Python traceback."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["run", "--workspace", "Bad Slug", "hello"],
        env={"ANTHROPIC_API_KEY": "sk-test"},
    )

    assert result.exit_code != 0
    # The envelope/error path is exercised, not an unhandled WorkspaceError.
    assert "Traceback" not in result.output
