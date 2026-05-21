"""Cross-language parity lint.

Ensures TS and Py conformance runners produce identical assertion outcomes
(same ``(kind, passed)`` tuples in order) for every YAML fixture.

This prevents the silent failure mode: "TS green / Py green but they're
testing *different* things" (design §4.6 H6 mitigation).

Usage::

    uv run pytest tests/test_conformance_parity.py -m integration -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _REPO_ROOT / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"

_RUNNER_PY = _REPO_ROOT / "wrappers" / "conformance" / "runner_py.py"
_TS_DIR = _REPO_ROOT / "wrappers" / "conformance"

# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _run_py(fixture_path: Path) -> dict:
    """Run the Python conformance runner and return the parsed JSON report."""
    result = subprocess.run(
        ["uv", "run", "python", str(_RUNNER_PY), str(fixture_path)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Exit code 0 = all passed, 1 = some failed — both are valid runs.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"Python runner failed unexpectedly (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return json.loads(result.stdout.strip())


def _run_ts(fixture_path: Path) -> dict:
    """Run the TypeScript conformance runner and return the parsed JSON report."""
    result = subprocess.run(
        ["pnpm", "exec", "tsx", "runner_ts.ts", str(fixture_path)],
        cwd=str(_TS_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Exit code 0 = all passed, 1 = some failed — both are valid runs.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"TypeScript runner failed unexpectedly (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# Parametrized parity test
# ---------------------------------------------------------------------------


def _all_fixtures() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("*.yaml"))


@pytest.mark.integration
@pytest.mark.parametrize("fixture_path", _all_fixtures(), ids=lambda p: p.name)
def test_ts_and_py_runners_agree(fixture_path: Path) -> None:
    """Assert TS and Py runners produce identical (kind, passed) outcomes."""
    py_report = _run_py(fixture_path)
    ts_report = _run_ts(fixture_path)

    # Extract ordered (kind, passed) tuples from each runner.
    py_outcomes: list[tuple[str, bool]] = [(a["kind"], a["passed"]) for a in py_report["assertions"]]
    ts_outcomes: list[tuple[str, bool]] = [(a["kind"], a["passed"]) for a in ts_report["assertions"]]

    # Build a diff message for clear diagnostics on failure.
    if py_outcomes != ts_outcomes:
        lines = [
            f"Fixture: {fixture_path.name}",
            "",
            "Assertion outcomes diverge between Py and TS runners:",
            f"  Py : {py_outcomes}",
            f"  TS : {ts_outcomes}",
            "",
            "Per-assertion diff:",
        ]
        max_len = max(len(py_outcomes), len(ts_outcomes))
        for i in range(max_len):
            py_item = py_outcomes[i] if i < len(py_outcomes) else "<missing>"
            ts_item = ts_outcomes[i] if i < len(ts_outcomes) else "<missing>"
            match = "✓" if py_item == ts_item else "✗"
            lines.append(f"  [{i}] {match}  Py={py_item}  TS={ts_item}")
        diff_msg = "\n".join(lines)
        pytest.fail(diff_msg)

    # Also assert the top-level passed flag agrees.
    assert py_report["passed"] == ts_report["passed"], (
        f"Fixture {fixture_path.name}: top-level 'passed' flag differs: "
        f"Py={py_report['passed']} TS={ts_report['passed']}"
    )
