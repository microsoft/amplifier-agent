import re

from typer.testing import CliRunner

from amplifier_agent_evaluations.cli import app


def test_help_lists_only_run() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert re.search(r"\brun\b", result.output)
    assert "regrade" not in result.output
    assert not re.search(r"\bgrade\b", result.output)


def test_run_accepts_parallel_and_bake_switches() -> None:
    result = CliRunner().invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    for option in ("--parallel", "--rebake", "--no-bake", "--keep-images"):
        assert option in result.output
    for option in ("--tasks", "--trials", "--agent"):
        assert option not in result.output


def test_rebake_and_no_bake_conflict() -> None:
    result = CliRunner().invoke(app, ["run", "runs/smoke-checkout.yaml", "--rebake", "--no-bake"])
    assert result.exit_code == 2


def test_keep_images_refuses_negative() -> None:
    result = CliRunner().invoke(app, ["run", "runs/smoke-checkout.yaml", "--keep-images", "-1"])
    assert result.exit_code == 2
