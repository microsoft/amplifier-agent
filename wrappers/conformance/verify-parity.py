#!/usr/bin/env python3
"""Cross-language conformance verifier for the Amplifier agent wire protocol.

WHY THIS IS A PLAIN SCRIPT AND NOT A PYTEST TEST
------------------------------------------------
`tests/` in this repo means "end-to-end contract tests" and nothing else.  The
conformance suite is a *component*, not a test: its fixtures live next to it
(``src/amplifier_agent_lib/protocol/conformance/fixtures/``), its two runners
live in this directory, and the driver that runs both and diffs them belongs
here too.  That is how every serious cross-language conformance suite is laid
out (gRPC interop tests, OpenTelemetry, CommonMark): fixtures + runners +
driver ship as one unit.  Making this a plain script makes it structurally
impossible to confuse with a test, and impossible to delete along with the
test suite.

WHAT IT VERIFIES
----------------
1. FRESHNESS    - every fixture in the canonical directory loads cleanly under
   ``protocol.conformance.loader.load_fixture`` (no FixtureValidationError),
   and ``runner_py.py`` exits 0/1 and emits a parseable JSON report with
   ``fixture`` / ``assertions`` / ``passed`` keys.
2. CONFORMANCE  - every fixture actually PASSES in both runners.  A fixture
   reporting ``passed: false`` is a hard failure in either runner.
3. PARITY       - for every fixture, the Python runner and the TypeScript
   runner produce the *identical ordered* list of ``(kind, passed)`` assertion
   tuples and the identical top-level ``passed`` flag.

WHY CONFORMANCE IS CHECKED SEPARATELY FROM PARITY (do not remove this either)
-----------------------------------------------------------------------------
Parity alone only proves the two runners AGREE - not that they agree on
success.  Two runners that both report ``passed: false`` have perfect parity
and a broken protocol.  A bad fixture edit, or a bug shared by both
evaluators, would sail straight through a parity-only check.

This is the property the deleted mirror suites
(``wrappers/conformance/tests/`` and ``wrappers/conformance/test/``) used to
hold: they asserted ``report.passed === true`` for a hardcoded list of seven
fixtures.  That list had to be updated by hand whenever a fixture was added,
and three of the ten current fixtures were never in it.  Checking it here
instead covers every fixture automatically and needs no maintenance.

WHY FRESHNESS IS CHECKED SEPARATELY FROM PARITY (do not remove this)
--------------------------------------------------------------------
Regression class debugged on 2026-05-24: ``runner_py.py`` became unimportable
because ``amplifier_agent_client.jsonrpc`` was deleted in the Phase B Mode A
pivot without updating the runner.  The runner then exited non-zero with EMPTY
stdout, ``json.loads("")`` raised JSONDecodeError, and every parity check
surfaced as a *framework error* rather than a conformance failure.  A broken
runner must never be able to masquerade as "nothing to report".

Design invariant this script enforces:

    "The conformance runners MUST produce a valid JSON report for every YAML
     fixture in the canonical fixture directory, and this script MUST fail
     loudly - never report green - when they cannot."

Concretely: an empty fixture set is a FAILURE, a crashed runner is a FAILURE,
unparseable output is a FAILURE, a fixture that fails in either runner is a
FAILURE, and missing pnpm dependencies are a FAILURE with instructions - none
of these are ever silently skipped.

USAGE
-----
    uv run python wrappers/conformance/verify-parity.py     # from repo root
    uv run python verify-parity.py                          # from this dir

    -v/--verbose  also print each fixture's per-assertion outcomes.

Exit code 0 = everything verified, 1 = at least one failure.

If the TypeScript runner cannot start, run::

    cd wrappers/conformance && pnpm install
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths - resolved from __file__, so the script runs from any working directory
# ---------------------------------------------------------------------------

_CONFORMANCE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _CONFORMANCE_DIR.parents[1]
_FIXTURE_DIR = _REPO_ROOT / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
_RUNNER_PY = _CONFORMANCE_DIR / "runner_py.py"
_RUNNER_TS = _CONFORMANCE_DIR / "runner_ts.ts"

_SUBPROCESS_TIMEOUT = 60

_PNPM_HINT = f"Run `pnpm install` in {_CONFORMANCE_DIR} and try again."


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RunnerResult:
    """Outcome of invoking one runner against one fixture."""

    report: dict[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.report is not None

    def outcomes(self) -> list[tuple[str, bool]]:
        assert self.report is not None
        return [(a["kind"], a["passed"]) for a in self.report["assertions"]]


@dataclass
class FixtureResult:
    """Aggregated verification outcome for a single fixture."""

    name: str
    loader: str = "-"
    py: str = "-"
    ts: str = "-"
    parity: str = "-"
    conformance: str = "-"
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _preflight() -> list[str]:
    """Return a list of blocking environment problems (empty means good to go)."""
    problems: list[str] = []

    if not _FIXTURE_DIR.is_dir():
        problems.append(f"Fixture directory not found: {_FIXTURE_DIR}")
    if not _RUNNER_PY.is_file():
        problems.append(f"Python runner not found: {_RUNNER_PY}")
    if not _RUNNER_TS.is_file():
        problems.append(f"TypeScript runner not found: {_RUNNER_TS}")

    if shutil.which("uv") is None:
        problems.append("`uv` not found on PATH. Install uv to run the Python conformance runner.")

    if shutil.which("pnpm") is None:
        problems.append(f"`pnpm` not found on PATH. Install pnpm, then: {_PNPM_HINT}")
    elif not (_CONFORMANCE_DIR / "node_modules").is_dir():
        problems.append(f"TypeScript dependencies are not installed (no node_modules/). {_PNPM_HINT}")

    return problems


# ---------------------------------------------------------------------------
# Guard 1: loader compatibility
# ---------------------------------------------------------------------------


def _check_loader(fixtures: list[Path]) -> dict[str, str | None]:
    """Load every fixture with the canonical loader.

    Returns a mapping of fixture name -> error string (None when it loaded).

    The loader enforces the scripted-replay fixture schema.  A fixture in a
    different shape makes the runners crash with empty stdout, which is exactly
    the silent-green failure mode this script exists to prevent.
    """
    try:
        from amplifier_agent_lib.protocol.conformance.loader import FixtureValidationError, load_fixture
    except ImportError as exc:
        raise SystemExit(
            f"ERROR: cannot import the conformance loader ({exc}).\n"
            f"Run this script through uv so the project environment is active:\n"
            f"  uv run python {_RUNNER_PY.parent.name}/verify-parity.py"
        ) from exc

    results: dict[str, str | None] = {}
    for path in fixtures:
        try:
            load_fixture(path)
            results[path.name] = None
        except FixtureValidationError as exc:
            results[path.name] = f"FixtureValidationError: {exc}"
        except Exception as exc:
            results[path.name] = f"unexpected {type(exc).__name__}: {exc}"
    return results


# ---------------------------------------------------------------------------
# Guard 2: runners produce valid reports
# ---------------------------------------------------------------------------


def _validate_report(stdout: str, stderr: str, returncode: int, label: str) -> RunnerResult:
    """Turn raw runner output into a RunnerResult, failing loudly on anything odd."""
    # Exit code 0 = all assertions passed, 1 = some failed. Both are valid runs.
    # Anything else means a crash (ImportError, unhandled exception, ...).
    if returncode not in (0, 1):
        return RunnerResult(
            None,
            f"{label} runner CRASHED (exit {returncode}) - this is a broken runner, not a "
            f"conformance failure.\nstdout: {stdout!r}\nstderr:\n{stderr}",
        )
    try:
        report = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return RunnerResult(
            None,
            f"{label} runner produced non-JSON output - it almost certainly crashed before "
            f"printing.\nstdout: {stdout!r}\nstderr:\n{stderr}",
        )
    if not isinstance(report, dict):
        return RunnerResult(None, f"{label} runner report is not a JSON object: {report!r}")
    missing = [k for k in ("fixture", "assertions", "passed") if k not in report]
    if missing:
        return RunnerResult(None, f"{label} runner report missing key(s) {missing}: {report!r}")
    if not isinstance(report["assertions"], list):
        return RunnerResult(None, f"{label} runner report 'assertions' is not a list: {report!r}")
    for i, assertion in enumerate(report["assertions"]):
        if not isinstance(assertion, dict) or "kind" not in assertion or "passed" not in assertion:
            return RunnerResult(None, f"{label} runner assertion[{i}] malformed: {assertion!r}")
    return RunnerResult(report, None)


def _run_py(fixture_path: Path) -> RunnerResult:
    """Run runner_py.py against a fixture and validate its report."""
    try:
        proc = subprocess.run(
            ["uv", "run", "python", str(_RUNNER_PY), str(fixture_path)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return RunnerResult(None, f"Python runner timed out after {_SUBPROCESS_TIMEOUT}s")
    return _validate_report(proc.stdout, proc.stderr, proc.returncode, "Python")


def _run_ts(fixture_path: Path) -> RunnerResult:
    """Run runner_ts.ts against a fixture and validate its report."""
    try:
        proc = subprocess.run(
            ["pnpm", "exec", "tsx", "runner_ts.ts", str(fixture_path)],
            cwd=str(_CONFORMANCE_DIR),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return RunnerResult(None, f"TypeScript runner timed out after {_SUBPROCESS_TIMEOUT}s")
    result = _validate_report(proc.stdout, proc.stderr, proc.returncode, "TypeScript")
    if result.error is not None and "Cannot find module" in (proc.stderr or ""):
        result.error += f"\n\nThis looks like missing dependencies. {_PNPM_HINT}"
    return result


# ---------------------------------------------------------------------------
# Guard 3: parity
# ---------------------------------------------------------------------------


def _parity_diff(name: str, py: RunnerResult, ts: RunnerResult) -> str | None:
    """Return a diff message when the two runners disagree, else None."""
    py_outcomes = py.outcomes()
    ts_outcomes = ts.outcomes()

    problems: list[str] = []

    if py_outcomes != ts_outcomes:
        lines = [
            "Assertion outcomes diverge between the Python and TypeScript runners:",
            f"  Py : {py_outcomes}",
            f"  TS : {ts_outcomes}",
            "",
            "Per-assertion diff:",
        ]
        for i in range(max(len(py_outcomes), len(ts_outcomes))):
            py_item = py_outcomes[i] if i < len(py_outcomes) else "<missing>"
            ts_item = ts_outcomes[i] if i < len(ts_outcomes) else "<missing>"
            mark = "ok  " if py_item == ts_item else "DIFF"
            lines.append(f"  [{i}] {mark}  Py={py_item}  TS={ts_item}")
        problems.append("\n".join(lines))

    assert py.report is not None and ts.report is not None
    if py.report["passed"] != ts.report["passed"]:
        problems.append(f"Top-level 'passed' flag differs: Py={py.report['passed']} TS={ts.report['passed']}")

    if not problems:
        return None
    return f"Fixture: {name}\n\n" + "\n\n".join(problems)


# ---------------------------------------------------------------------------
# Guard 4: conformance (a fixture must actually PASS, not merely agree)
# ---------------------------------------------------------------------------


def _conformance_failure(
    name: str,
    py_report: dict[str, Any],
    ts_report: dict[str, Any],
) -> str | None:
    """Return a failure message if either runner reports ``passed: false``.

    Parity is necessary but NOT sufficient: two runners that both fail a
    fixture agree perfectly.  This is the check that makes a genuinely broken
    fixture or a bug shared by both evaluators visible.
    """
    failing_langs = [
        label for label, report in (("Python", py_report), ("TypeScript", ts_report)) if not report["passed"]
    ]
    if not failing_langs:
        return None

    lines = [
        f"Fixture: {name}",
        "",
        f"Fixture does NOT pass conformance in: {', '.join(failing_langs)}",
        "(Parity is not enough - a fixture both runners fail is still broken.)",
        "",
        "Failing assertions:",
    ]
    for label, report in (("Py", py_report), ("TS", ts_report)):
        for i, assertion in enumerate(report["assertions"]):
            if not assertion["passed"]:
                detail = assertion.get("detail", "")
                lines.append(f"  {label} [{i}] {assertion['kind']}: {detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_table(results: list[FixtureResult]) -> None:
    name_w = max([len(r.name) for r in results] + [len("FIXTURE")])
    header = f"{'FIXTURE':<{name_w}}  {'LOADER':<8}{'PY':<8}{'TS':<8}{'PARITY':<8}{'CONFORM':<9}RESULT"
    print(header)
    print("-" * len(header))
    for r in results:
        status = "OK" if r.passed else "FAIL"
        print(f"{r.name:<{name_w}}  {r.loader:<8}{r.py:<8}{r.ts:<8}{r.parity:<8}{r.conformance:<9}{status}")


def _print_verbose(name: str, py: RunnerResult, ts: RunnerResult) -> None:
    if py.report is None or ts.report is None:
        return
    print(f"\n  {name}")
    for i, assertion in enumerate(py.report["assertions"]):
        print(f"    [{i}] {assertion['kind']:<24} passed={assertion['passed']}  {assertion.get('detail', '')}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-parity.py",
        description="Verify the Python and TypeScript conformance runners agree on the wire protocol.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print per-assertion detail")
    args = parser.parse_args(argv[1:])

    print("Amplifier protocol conformance: cross-language parity verification")
    print(f"  fixtures : {_FIXTURE_DIR}")
    print(f"  runners  : {_RUNNER_PY.name}, {_RUNNER_TS.name}")
    print()

    problems = _preflight()
    if problems:
        sys.stdout.flush()
        print("PREFLIGHT FAILED - cannot verify anything:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    fixtures = sorted(_FIXTURE_DIR.glob("*.yaml"))
    # An empty fixture set is a failure, never a silent pass: "verified nothing"
    # must not be reportable as green.
    if not fixtures:
        print(f"FAILED: no YAML fixtures found in {_FIXTURE_DIR}", file=sys.stderr)
        return 1

    loader_errors = _check_loader(fixtures)

    results: list[FixtureResult] = []
    failures: list[str] = []

    for fixture_path in fixtures:
        result = FixtureResult(name=fixture_path.name)

        loader_error = loader_errors[fixture_path.name]
        if loader_error is not None:
            result.loader = "FAIL"
            result.failures.append(f"loader validation failed: {loader_error}")
            results.append(result)
            failures.append(f"[{result.name}] loader validation failed: {loader_error}")
            # A fixture the loader rejects will crash runner_py.py; running it
            # would only produce a confusing secondary error.
            continue
        result.loader = "ok"

        py = _run_py(fixture_path)
        ts = _run_ts(fixture_path)

        py_report = py.report
        if py_report is None:
            result.py = "CRASH"
            result.failures.append(py.error or "unknown Python runner failure")
            failures.append(f"[{result.name}] {py.error}")
        else:
            result.py = "pass" if py_report["passed"] else "fail"

        ts_report = ts.report
        if ts_report is None:
            result.ts = "CRASH"
            result.failures.append(ts.error or "unknown TypeScript runner failure")
            failures.append(f"[{result.name}] {ts.error}")
        else:
            result.ts = "pass" if ts_report["passed"] else "fail"

        if py_report is not None and ts_report is not None:
            diff = _parity_diff(result.name, py, ts)
            if diff is None:
                result.parity = "match"
            else:
                result.parity = "DIFF"
                result.failures.append(diff)
                failures.append(diff)

            # CONFORMANCE gate. Parity only proves the runners AGREE; it does
            # not prove they agree on SUCCESS. Two runners both reporting
            # passed=false have perfect parity and a broken protocol, so a
            # failing fixture is a hard failure independent of the diff above.
            failing = _conformance_failure(result.name, py_report, ts_report)
            if failing is None:
                result.conformance = "ok"
            else:
                result.conformance = "FAIL"
                result.failures.append(failing)
                failures.append(failing)

            if args.verbose:
                _print_verbose(result.name, py, ts)

        results.append(result)

    if args.verbose:
        print()

    _print_table(results)

    verified = sum(1 for r in results if r.passed)
    print()
    print(f"{verified}/{len(results)} fixtures verified (loader + both runners + parity).")

    # Defence in depth: if for any reason nothing was actually checked, fail.
    if not results:
        print("FAILED: no fixtures were checked.", file=sys.stderr)
        return 1

    if failures:
        print()
        print(f"FAILED: {len(failures)} problem(s) found.", file=sys.stderr)
        for problem in failures:
            print("\n" + "=" * 72, file=sys.stderr)
            print(problem, file=sys.stderr)
        return 1

    print("PASSED: Python and TypeScript runners agree on every fixture.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
