"""Command line entry point for the amplifier-agent evaluations."""

from pathlib import Path
from typing import Annotated

import typer

from amplifier_agent_evaluations import bake, runner

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def cli() -> None:
    """Live capability evaluations of amplifier-agent in isolated containers."""


@app.command()
def run(
    profile: Annotated[Path, typer.Argument(help="Run profile, e.g. runs/smoke-github.yaml.")],
    parallel: Annotated[int | None, typer.Option(help="Trials at once, replacing the profile's.")] = None,
    rebake: Annotated[bool, typer.Option(help="Bake the agent image even when one with the same key exists.")] = False,
    no_bake: Annotated[
        bool, typer.Option(help="Install the agent in every trial instead of using a baked image.")
    ] = False,
    keep_images: Annotated[
        int, typer.Option(min=0, help="Baked images per repository and grader caches to keep; 0 keeps all.")
    ] = bake.KEEP_IMAGES,
) -> None:
    """Run one profile: preflight, snapshot or upstream sha, image bake, trials in parallel, summary.

    Exits 0 when every trial passed, 1 otherwise.
    """
    if rebake and no_bake:
        raise typer.BadParameter("--rebake and --no-bake cannot be combined")
    raise typer.Exit(code=runner.run(profile, parallel, rebake=rebake, no_bake=no_bake, keep_images=keep_images))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
