"""Run deterministic public API verification through the conformance kit.

Lint, formatting and type checks are prek hooks; run `prek run --all-files` for those.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Require every contract obligation.")
    parser.add_argument("--runtime", action="store_true", help="Build and test the TypeScript fixture runtime.")
    args = parser.parse_args()
    from conformance.run import main as conformance_main

    sys.argv = ["conformance/run.py", "--output", str(ROOT / "build/conformance.json")]
    if args.full:
        sys.argv.append("--full")
    if args.runtime or args.full:
        sys.argv.append("--build-runtime")
    conformance_main()


if __name__ == "__main__":
    main()
