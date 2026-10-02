"""Run lint, type checks, and tests for the Python packages, the HTTP face, and TypeScript."""

import argparse
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TYPESCRIPT = ROOT / "packages" / "typescript"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Run from the checkout with: uv run --all-packages python scripts/check.py",
    )
    parser.add_argument("--runtime", action="store_true", help="Rebuild the TypeScript test runtime before testing.")
    args = parser.parse_args()
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        parser.error("pnpm not found. Install Node 22 and pnpm (npm install --global pnpm@11.25.0), then retry.")
    python = [sys.executable, "-m"]
    steps = [
        ([*python, "ruff", "check"], ROOT),
        ([*python, "ruff", "format", "--check"], ROOT),
        ([*python, "ty", "check"], ROOT),
        # Run inside the package rather than with --dir: a corepack-managed pnpm picks the pinned
        # version from the directory it starts in.
        ([pnpm, "check"], TYPESCRIPT),
    ]
    if args.runtime:
        output = TYPESCRIPT / "runtime" / "linux-x64"
        steps.append(([sys.executable, "scripts/build_runtime.py", "--test", "--output", str(output)], ROOT))
    steps += [([*python, "pytest"], ROOT), ([pnpm, "test"], TYPESCRIPT)]
    for command, cwd in steps:
        print(f"$ {shlex.join(command)}", flush=True)
        if subprocess.run(command, cwd=cwd, check=False).returncode != 0:
            raise SystemExit(f"Failed: {shlex.join(command)}")


if __name__ == "__main__":
    main()
