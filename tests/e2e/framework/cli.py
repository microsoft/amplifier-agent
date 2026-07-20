"""E2E harness entry point — invoked as ``uv run python tests/e2e/framework/cli.py <verb>``.

Verbs:
    up       Cold-provision the warm DTU (Gitea mirror + launch + wait ready).
    refresh  Re-push local repos and reinstall in place inside the warm DTU.
    run      Ensure warm (auto-`up` unless --skip-setup), then run the e2e pytest suite.
             Optionally scope to one or more features: ``run skills``, ``run run modes``.
    down     Tear down the DTU instance (leaves Gitea running).

Not installed as a console script; runs directly via uv run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Make the `framework` package importable when this file is run directly as a script
# (tests/e2e/framework/cli.py -> parents[1] = tests/e2e, the package's parent dir).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import click

from framework import dtu_manager
from framework.progress import log

# tests/e2e/framework/cli.py -> framework -> e2e -> tests -> amplifier-agent (repo root)
REPO_ROOT = Path(__file__).resolve().parents[3]
SUITES_ROOT = REPO_ROOT / "tests" / "e2e" / "suites"

# Required host tools mapped to the command that installs each one.
_PREREQS = {
    "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "amplifier-digital-twin": (
        "uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main"
    ),
    "amplifier-gitea": "uv tool install git+https://github.com/microsoft/amplifier-bundle-gitea@main",
    "incus": "install Incus per @digital-twin-universe:docs/installing-incus.md, then `incus admin init`",
    "docker": "install Docker (WSL2: Docker Desktop with WSL integration) and ensure the daemon is running",
}


def _preflight() -> None:
    """Fail loud if any required host tool is missing, with install hints."""
    missing: list[str] = []
    for tool, hint in _PREREQS.items():
        if shutil.which(tool) is None:
            missing.append(f"  - {tool}: {hint}")
    if missing:
        raise click.ClickException("missing required host tools:\n" + "\n".join(missing))


def _valid_features() -> set[str]:
    """Feature names are the immediate subdirectories of tests/e2e/suites/."""
    if not SUITES_ROOT.is_dir():
        return set()
    return {p.name for p in SUITES_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}


def _split_features(args: tuple[str, ...]) -> tuple[list[str], tuple[str, ...]]:
    """Consume leading bare-word tokens as feature names; stop at the first dashed or path-like token.

    Bare words with no leading dash are unambiguously meant as feature selectors (real
    pytest passthrough uses ``-k``/``-x``/``-m`` flags or ``path::node`` selectors), so an
    unrecognized bare word is a hard error rather than silently falling through to pytest.
    """
    valid = _valid_features()
    features: list[str] = []
    idx = 0
    for token in args:
        if token.startswith("-") or "/" in token or "::" in token:
            break
        if token not in valid:
            raise click.ClickException(
                f"unknown feature {token!r}; valid features: {', '.join(sorted(valid)) or '(none found)'}"
            )
        features.append(token)
        idx += 1
    return features, args[idx:]


@click.group()
def cli() -> None:
    """amplifier-agent e2e harness."""


@cli.command()
def up() -> None:
    """Provision a fresh warm DTU (destroys any existing aa-e2e) and print the state JSON."""
    _preflight()
    new_state = dtu_manager.provision()
    click.echo(json.dumps(new_state, indent=2))


@cli.command()
def refresh() -> None:
    """Re-push local repos and reinstall in place inside the warm DTU."""
    dtu_manager.refresh()
    click.echo("refreshed")


@cli.command(context_settings={"ignore_unknown_options": True})
@click.option("--skip-setup", is_flag=True, help="Skip the Gitea push + fresh rebuild; run against the DTU as-is.")
@click.option("--ephemeral", is_flag=True, help="Tear down the DTU after the run.")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run(skip_setup: bool, ephemeral: bool, args: tuple[str, ...]) -> None:
    """Push latest code, provision a fresh DTU with it, then run the e2e pytest suite.

    By default every run re-mirrors the working tree to Gitea and rebuilds the DTU clean
    (~90s), so the suite always runs against the latest code with a working CLI and server.
    Use --skip-setup for a fast re-run against the existing DTU as-is.

    Optionally scope the run to one or more features (directories under tests/e2e/suites/),
    e.g. ``cli.py run skills`` or ``cli.py run run modes``. Any remaining args (flags, `-k`
    expressions, explicit node ids) pass straight through to pytest.
    """
    _preflight()

    features, pytest_args = _split_features(args)

    if skip_setup:
        if not dtu_manager.is_warm():
            raise click.ClickException("no warm DTU and --skip-setup set; run `up` first")
        log("run: --skip-setup; using existing warm DTU as-is")
    else:
        dtu_manager.provision()

    if features:
        targets = [f"tests/e2e/suites/{feature}" for feature in features]
        log(f"run: scoping to feature(s): {', '.join(features)}")
    else:
        targets = ["tests/e2e/suites"]

    try:
        log("run: launching pytest suite (starts the in-DTU HTTP server, then runs cases)...")
        proc = subprocess.run(
            ["uv", "run", "pytest", *targets, "-m", "dtu", "-ra", *pytest_args],
            cwd=str(REPO_ROOT),
        )
        log(f"run: pytest finished (exit {proc.returncode})")
    finally:
        if ephemeral:
            dtu_manager.teardown()

    sys.exit(proc.returncode)


@cli.command()
def down() -> None:
    """Destroy the DTU instance (leaves Gitea running)."""
    dtu_manager.teardown()
    click.echo("torn down")


if __name__ == "__main__":
    cli()
