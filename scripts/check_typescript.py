"""Lint, format and type check packages/typescript through its own pnpm."""

from pathlib import Path
import shutil
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "typescript"


def main() -> None:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print(
            "pnpm not found. packages/typescript changed, so its checks must run: "
            "install Node 22 and pnpm (npm install --global pnpm@11.25.0), then retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    # Run inside the package rather than with --dir: a corepack-managed pnpm picks the pinned
    # version from the directory it starts in.
    subprocess.run([pnpm, "check"], cwd=PACKAGE, check=True)


if __name__ == "__main__":
    main()
