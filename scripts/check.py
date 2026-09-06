"""Run source checks and deterministic public API verification."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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
            "conformance",
            "conftest.py",
        ],
        ["pyright", "--pythonpath", sys.executable, *SOURCES],
    ]
    if not (args.runtime or args.full):
        compiler = str(ROOT / "packages/typescript/node_modules/.bin/tsc")
        commands.extend([
            [compiler, "-p", "packages/typescript/tsconfig.json", "--noEmit"],
            [compiler, "-p", "packages/typescript/tsconfig.build.json"],
        ])
    for command in commands:
        print("Running: " + " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=ROOT, timeout=120)
        if completed.returncode:
            raise SystemExit(completed.returncode)
    from conformance.run import main as conformance_main

    sys.argv = ["conformance/run.py", "--output", str(ROOT / "build/conformance.json")]
    if args.full:
        sys.argv.append("--full")
    if args.runtime or args.full:
        sys.argv.append("--build-runtime")
    conformance_main()


if __name__ == "__main__":
    main()
