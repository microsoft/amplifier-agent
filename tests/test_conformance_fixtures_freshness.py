"""Conformance fixture freshness guard.

Prevents the regression class debugged on 2026-05-24: the Python conformance
runner (runner_py.py) became unimportable because ``amplifier_agent_client.jsonrpc``
was deleted in the Phase B Mode A pivot without updating the runner.  Every
parity test then silently failed: the runner exited 1 with an empty stdout,
``json.loads("")`` raised JSONDecodeError, and 9 parity tests plus the exit-gate
test appeared as framework errors rather than conformance failures.

Design invariant this file enforces:
  "The Python conformance runner MUST produce a valid JSON conformance report
   when invoked against any YAML fixture in the canonical fixture directory."

This is the load-bearing property: if the runner crashes (import error, schema
error, or runtime exception), it produces empty stdout, and the parity test
silently reports the wrong thing.  This test catches that failure mode directly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants (mirrors test_conformance_parity.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _REPO_ROOT / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
_RUNNER_PY = _REPO_ROOT / "wrappers" / "conformance" / "runner_py.py"


# ---------------------------------------------------------------------------
# Guard 1: runner produces valid JSON for every fixture
# ---------------------------------------------------------------------------


def _all_fixtures() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("*.yaml"))


@pytest.mark.parametrize("fixture_path", _all_fixtures(), ids=lambda p: p.name)
def test_runner_py_produces_valid_json_for_each_fixture(fixture_path: Path) -> None:
    """Each fixture in the canonical dir must yield a parseable JSON report.

    Guards against:
    - Import errors in runner_py.py (broken dependency → empty stdout)
    - Fixture schema validation failures (FixtureValidationError → empty stdout)
    - Unhandled runtime exceptions in the runner

    If this test fails, the parity test (test_conformance_parity.py) will also
    fail with a cryptic JSONDecodeError rather than a meaningful assertion diff.
    Fix the runner or the fixture before investigating parity failures.
    """
    result = subprocess.run(
        ["uv", "run", "python", str(_RUNNER_PY), str(fixture_path)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Exit codes 0 (all passed) and 1 (some failed) are both valid.
    # Any other exit code indicates a crash — import error, unhandled exception, etc.
    assert result.returncode in (0, 1), (
        f"runner_py.py exited {result.returncode} for fixture {fixture_path.name}.\n"
        f"A non-0/1 exit code indicates a crash (e.g. ModuleNotFoundError).\n"
        f"stderr:\n{result.stderr}"
    )
    try:
        report = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"runner_py.py produced non-JSON output for {fixture_path.name}.\n"
            f"This typically means the runner crashed before printing anything.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr:\n{result.stderr}"
        ) from exc

    assert "fixture" in report, f"Report missing 'fixture' key: {report}"
    assert "assertions" in report, f"Report missing 'assertions' key: {report}"
    assert "passed" in report, f"Report missing 'passed' key: {report}"


# ---------------------------------------------------------------------------
# Guard 2: all fixtures are structurally loadable by the canonical loader
# ---------------------------------------------------------------------------


def test_all_canonical_fixtures_are_loader_compatible() -> None:
    """Every YAML in the canonical fixture directory must pass loader validation.

    The loader (amplifier_agent_lib.protocol.conformance.loader.load_fixture)
    enforces the scripted-replay fixture schema.  If a fixture is added that
    uses a different schema (e.g. 'real-binary' test_type without a 'script'
    key), the loader raises FixtureValidationError, the runner crashes, and
    the parity test produces a JSONDecodeError instead of a useful diff.

    Adding a fixture in a new format requires either:
    (a) updating the loader to accept the new format, OR
    (b) placing the fixture in a separate directory with its own runner.
    """
    from amplifier_agent_lib.protocol.conformance.loader import FixtureValidationError, load_fixture

    fixtures = _all_fixtures()
    assert fixtures, f"No YAML fixtures found in {_FIXTURE_DIR}"

    errors: list[str] = []
    for fixture_path in fixtures:
        try:
            load_fixture(fixture_path)
        except FixtureValidationError as exc:
            errors.append(f"  {fixture_path.name}: {exc}")
        except Exception as exc:
            errors.append(f"  {fixture_path.name}: unexpected error — {type(exc).__name__}: {exc}")

    if errors:
        raise AssertionError(
            f"{len(errors)} fixture(s) failed loader validation:\n"
            + "\n".join(errors)
            + "\n\nFix the fixture schema or update the loader to accept the new format."
        )
