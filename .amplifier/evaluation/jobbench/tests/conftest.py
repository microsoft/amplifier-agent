"""Make `jobbench` importable without installing the package.

run.py does the same sys.path insert at its own entry point; tests need it
too since pytest doesn't go through run.py.

The harness root goes on the path as well so `import run` works: run.py is
the CLI shell, not a package module, but it owns real logic worth pinning
(see test_judge_attribution.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))
