import re

from typer.testing import CliRunner

from amplifier_agent_evaluations.cli import app


def test_help_lists_only_run() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert re.search(r"\brun\b", result.output)
    assert "regrade" not in result.output
    assert not re.search(r"\bgrade\b", result.output)


def test_run_accepts_only_parallel() -> None:
    result = CliRunner().invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--parallel" in result.output
    for option in ("--tasks", "--trials", "--agent"):
        assert option not in result.output
