"""Entry point for the evaluation harness.

Puts the harness package (`src/eval`) on the import path, then dispatches to the
CLI. Run with:

    uv run python run.py validate
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from eval.cli import main  # noqa: E402  (path setup must precede this import)

if __name__ == "__main__":
    raise SystemExit(main())
