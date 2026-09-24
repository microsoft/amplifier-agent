"""Command line entry point for the amplifier-agent evaluations."""

from pathlib import Path
from typing import Annotated

import typer

from amplifier_agent_evaluations import runner

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def cli() -> None:
    """Live capability evaluations of amplifier-agent v1 in isolated containers."""


@app.command()
def run(
    profile: Annotated[Path, typer.Argument(help="Run profile, e.g. runs/smoke-github.yaml.")],
    parallel: Annotated[int | None, typer.Option(help="Trials at once, replacing the profile's.")] = None,
) -> None:
    """Run one profile: preflight, snapshot or upstream sha, trials in parallel, summary.

    Exits 0 when every trial passed, 1 otherwise.
    """
    raise typer.Exit(code=runner.run(profile, parallel))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
