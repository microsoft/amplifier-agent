"""Run source checks and the deterministic public-surface milestone."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ["packages/python/src", "packages/engine/src", "packages/http/src"]
TESTS = [
    "packages/python/tests",
    "packages/engine/tests",
    "packages/http/tests",
    "tests",
    "conformance/tests",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Require every contract obligation.")
    parser.add_argument(
        "--runtime", action="store_true", help="Build and test the TypeScript fixture runtime."
    )
    args = parser.parse_args()
    commands = [
        [
            "ruff",
            "check",
            *SOURCES,
            *TESTS,
            "scripts",
            "conformance/surface",
            "conformance/fixtures",
        ],
        ["pyright", "--pythonpath", sys.executable, *SOURCES],
        [sys.executable, "-m", "conformance.surface.check"],
        [sys.executable, "conformance/run.py", *(["--full"] if args.full else [])],
        ["pnpm", "--dir", "packages/typescript", "check"],
        ["pnpm", "--dir", "packages/typescript", "build"],
    ]
    if args.runtime:
        commands.extend(
            [
                [
                    sys.executable,
                    "scripts/build_runtime.py",
                    "--fixture",
                    "--output",
                    "packages/typescript/runtime/linux-x64",
                ],
                ["pnpm", "--dir", "packages/typescript", "test"],
            ]
        )
    for command in commands:
        print("Running: " + " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=ROOT, timeout=120)
        if completed.returncode:
            raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
