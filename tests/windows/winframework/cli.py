#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["click>=8.1"]
# ///
"""Windows e2e harness entry point.

    uv run tests/windows/winframework/cli.py doctor
    uv run tests/windows/winframework/cli.py up
    uv run tests/windows/winframework/cli.py run
    uv run tests/windows/winframework/cli.py run smoke
    uv run tests/windows/winframework/cli.py down

A self-contained uv script rather than a shell script, so the same command
works from WSL2 and from a native Windows shell. The only host-specific piece
is which docker binary gets invoked, and that lives in ``container.py``.

Verbs:
    doctor  Check prereqs and report what is missing. Changes nothing.
    up      Build the provisioned image and record it as warm.
    run     Ensure warm, then run the pytest suites. Scope with bare suite names.
    down    Remove the image and clear the warm record.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# tests/windows/winframework/cli.py -> framework -> windows -> tests -> repo root
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[3]
WINDOWS_ROOT = HERE.parents[1]
SUITES_ROOT = WINDOWS_ROOT / "winsuites"

# Make the sibling `framework` package importable when run as a bare script.
sys.path.insert(0, str(WINDOWS_ROOT))

import click  # noqa: E402

from winframework import container  # noqa: E402

# Host tools required before anything can run, mapped to how to get each.
_PREREQS = {
    "uv": "install uv: https://docs.astral.sh/uv/getting-started/installation/",
    "docker": (
        "install Docker Desktop with Windows containers enabled"
        " (from WSL2, also enable WSL integration for this distro)"
    ),
}


def _which_docker() -> str | None:
    try:
        return container.docker_exe()
    except container.WinE2EError:
        return None


def _valid_suites() -> set[str]:
    if not SUITES_ROOT.is_dir():
        return set()
    return {p.name for p in SUITES_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}


def _split_suites(args: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Consume leading bare words as suite names; the rest goes to pytest."""
    valid = _valid_suites()
    suites: list[str] = []
    index = 0
    for token in args:
        if token.startswith("-") or "/" in token or "::" in token:
            break
        if token not in valid:
            raise click.ClickException(
                f"unknown suite {token!r}; valid suites: {', '.join(sorted(valid)) or '(none found)'}"
            )
        suites.append(token)
        index += 1
    return suites, list(args[index:])


def _preflight() -> None:
    """Fail loud if the host cannot run this suite at all."""
    missing = []
    for tool, hint in _PREREQS.items():
        found = _which_docker() if tool == "docker" else shutil.which(tool)
        if not found:
            missing.append(f"  - {tool}: {hint}")
    if missing:
        raise click.ClickException("missing required host tools:\n" + "\n".join(missing))

    problems = container.preflight()
    if problems:
        raise click.ClickException("preflight failed:\n" + "\n".join(f"  - {p}" for p in problems))


@click.group()
def cli() -> None:
    """Windows-container e2e harness for amplifier-agent."""


@cli.command()
def doctor() -> None:
    """Check prereqs and report what is missing."""
    click.echo(f"host:      {sys.platform}{' (WSL)' if container.on_wsl() else ''}")

    exe = _which_docker()
    click.echo(f"docker:    {exe or 'NOT FOUND'}")
    click.echo(f"uv:        {shutil.which('uv') or 'NOT FOUND'}")
    click.echo(f"context:   {container.CONTEXT}")
    click.echo(f"base:      {container.BASE_IMAGE}")
    click.echo(f"image:     {container.IMAGE}")
    click.echo(f"agent ref: {container.AGENT_REF}")

    problems = container.preflight() if exe else ["docker not found"]
    if problems:
        click.echo("\nproblems:")
        for problem in problems:
            click.echo(f"  - {problem}")
        raise SystemExit(1)

    ostype, isolation = container.engine_info()
    click.echo(f"engine:    OSType={ostype} Isolation={isolation}")
    click.echo(f"built:     {'yes' if container.image_exists() else 'no  (run: up)'}")
    click.echo("\ndoctor: OK")


@cli.command()
@click.option("--rebuild", is_flag=True, help="Rebuild from scratch, ignoring the docker layer cache.")
def up(rebuild: bool) -> None:
    """Build the provisioned image."""
    _preflight()
    if container.image_exists() and not rebuild:
        click.echo(f"image already built: {container.IMAGE} (use --rebuild to force)")
    else:
        click.echo(f"building {container.IMAGE} from {container.BASE_IMAGE} (agent ref: {container.AGENT_REF})")
        click.echo("this installs git, uv, python and amplifier-agent; expect several minutes on a cold build")
        container.build_image(no_cache=rebuild)
        click.echo(f"built: {container.IMAGE}")


@cli.command(
    context_settings={"ignore_unknown_options": True},
)
@click.option("--skip-setup", is_flag=True, help="Require an existing image instead of building one.")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run(skip_setup: bool, args: tuple[str, ...]) -> None:
    """Run the suites. Scope with bare names: run smoke hello -x"""
    _preflight()
    suites, pytest_args = _split_suites(args)

    if not container.image_exists():
        if skip_setup:
            raise click.ClickException(f"no image {container.IMAGE} and --skip-setup set; run `up` first")
        click.echo("no image yet; building it first")
        container.build_image()

    targets = [f"tests/windows/winsuites/{s}" for s in suites] if suites else ["tests/windows/winsuites"]
    if suites:
        click.echo(f"scoping to suite(s): {', '.join(suites)}")

    proc = subprocess.run(
        ["uv", "run", "pytest", *targets, "-m", "windows", "-ra", *pytest_args],
        cwd=str(REPO_ROOT),
    )
    raise SystemExit(proc.returncode)


@cli.command()
def down() -> None:
    """Remove the provisioned image. The base image is left alone."""
    container.remove_image()
    click.echo(f"removed: {container.IMAGE}")


if __name__ == "__main__":
    cli()
