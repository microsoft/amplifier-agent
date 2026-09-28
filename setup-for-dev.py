# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Cross-platform script to set up a development environment for the project.
It assumes that you have installed all the prerequisites listed in docs/DEVELOPMENT.md.
"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).parent
TYPESCRIPT = ROOT / "packages" / "typescript"
# `uv run setup-for-dev.py` activates this script's own environment; the nested uv commands must target the project's.
ENVIRONMENT = {name: value for name, value in os.environ.items() if name != "VIRTUAL_ENV"}


def run(command: str, cwd: Path = ROOT) -> None:
    """Run a command, forwarding its output to this process's stdout and stderr."""
    subprocess.run(shlex.split(command), cwd=cwd, check=True, env=ENVIRONMENT)


def install_typescript_dependencies() -> None:
    """Only when pnpm is present; the TypeScript binding is one of the checked surfaces, so say so when it is skipped."""
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print(
            "pnpm not found; skipping packages/typescript. "
            "Install Node 22 and pnpm (npm install --global pnpm@11.25.0) to work on the TypeScript binding."
        )
        return
    # Run inside the package rather than with --dir: a corepack-managed pnpm picks the pinned
    # version from the directory it starts in.
    subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=TYPESCRIPT, check=True, env=ENVIRONMENT)
    # The tests import the package by name, which resolves to dist/.
    subprocess.run([pnpm, "build"], cwd=TYPESCRIPT, check=True, env=ENVIRONMENT)


def main() -> None:
    run("uv --version")
    run("prek --version")
    run("uv sync --frozen --all-packages --all-groups")
    run("uv sync --frozen --all-groups --directory evaluations")
    run("prek install")
    install_typescript_dependencies()


if __name__ == "__main__":
    main()
