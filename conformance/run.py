"""Report passing evidence, failed assertions, and missing contract requirements."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conformance.coverage import assess, obligations, read_requirements  # noqa: E402
from conformance.reviews import read_reviews  # noqa: E402


def junit_cases(path: Path, suite: str) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"Missing test results at {path}; run the complete registered suite")
    cases = []
    for case in ET.parse(path).iter("testcase"):
        module, name = case.get("classname", ""), case.get("name", "")
        if not name:
            raise ValueError(f"{path}: unnamed test case")
        status = "failed" if case.find("failure") is not None else (
            "setup_failed" if case.find("error") is not None else (
                "skipped" if case.find("skipped") is not None else "passed"
            )
        )
        identity = f"{module}::{name}" if suite != "typescript" and module else name
        cases.append({"suite": suite, "case": identity, "status": status})
    if not cases:
        raise ValueError(f"{path}: no test cases; collect and execute the registered tests")
    return cases


def typescript_cases(path: Path, suite: str = "typescript") -> list[dict]:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    summaries = [record for record in records if record.get("kind") == "summary"]
    if not summaries or type(summaries[-1].get("success")) is not bool:
        raise ValueError("TypeScript results have no complete test-run summary")
    cases = [record for record in records if record.get("kind") == "case"]
    if not cases or any(not isinstance(case.get("case"), str) or not case["case"]
                        or case.get("status") not in {"passed", "failed", "skipped"}
                        for case in cases):
        raise ValueError("TypeScript results have missing or malformed case outcomes")
    if not summaries[-1]["success"] and all(case["status"] == "passed" for case in cases):
        raise ValueError("TypeScript runner failed without a recorded failed case")
    return [{**case, "suite": suite} for case in cases]


def source_snapshot() -> dict[str, str]:
    paths = []
    for directory in ("contracts", "conformance", "scripts", "tests", "docs_v1"):
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and path.suffix in {".py", ".json", ".md", ".mjs"})
    for package in ("python", "engine", "http", "typescript"):
        base = ROOT / "packages" / package
        for directory in ("src", "test", "tests", "scripts"):
            paths.extend(path for path in (base / directory).rglob("*")
                         if path.is_file() and path.suffix in {".py", ".ts", ".mjs", ".json"})
        paths.extend(path for path in base.iterdir()
                     if path.is_file() and path.suffix in {".toml", ".json", ".yaml", ".md"})
    paths.extend(ROOT / name for name in ("README.md", "conftest.py", "pyproject.toml", "uv.lock"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def execution_environment(*, installed: bool) -> dict[str, str]:
    from conformance.artifacts import INPUTS

    environment = dict(os.environ)
    if not installed:
        for name in INPUTS:
            environment.pop(name, None)
    return environment


def execute(command: list[str], *, cwd: Path, timeout: int,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Stop the complete test process group when its deadline expires."""
    with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True, env=env) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, stdout, stderr) from None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def report(*, typescript: bool = False, installed: bool = False,
           build_runtime: bool = False) -> dict:
    started = datetime.now(UTC).isoformat()
    source = source_snapshot()
    pytest = [sys.executable, "-m", "pytest", "-q"]
    commands = [
        ("inventory", [sys.executable, "conformance/check_inventory.py"], None, ROOT),
        ("http-fixtures", [sys.executable, "conformance/http/check.py"], None, ROOT),
        ("surface", [sys.executable, "-m", "conformance.surface.check"], None, ROOT),
        ("python-binding", [*pytest, "packages/python/tests"], "python", ROOT),
        ("python-e2e", [*pytest, "tests/e2e/python", "--engine=production"], "python", ROOT),
        ("python-replacement", [*pytest, "tests/e2e/python", "--engine=replacement"],
         "python-replacement", ROOT),
        ("python-providers", [*pytest, "tests/integration"], "python", ROOT),
        ("engine", [*pytest, "packages/engine/tests"], "engine", ROOT),
        ("conformance", [*pytest, "conformance/tests"], "repository", ROOT),
        ("local-forge-unit", [*pytest, "tests/e2e/test_local_forge.py"], "repository", ROOT),
        ("http-unit", [*pytest, "packages/http/tests"], "http", ROOT),
        ("http-e2e", [*pytest, "tests/e2e/http", "--engine=production"], "http", ROOT),
        ("http-replacement", [*pytest, "tests/e2e/http", "--engine=replacement"],
         "http-replacement", ROOT),
    ]
    if typescript:
        from conformance.check_replacement import PUBLIC_TEST_FILES

        package = ROOT / "packages/typescript"
        compiler = str(package / "node_modules/.bin/tsc")
        if build_runtime:
            commands.append(("fixture-build", [sys.executable, "scripts/build_runtime.py",
                             "--fixture", "--output", str(package / "runtime/linux-x64")],
                             None, ROOT))
        commands.extend([
            ("typescript-types", [compiler, "--noEmit"], None, package),
            ("typescript-build", [compiler, "-p", "tsconfig.build.json"], None, package),
        ])
        node = ["node", "--import", "tsx", "--test",
                "--test-reporter=../../conformance/typescript_reporter.mjs"]
        for name, suite, files in (
            ("typescript-runtime", "typescript", PUBLIC_TEST_FILES),
            ("typescript-surface", "typescript", (
                "records.test.ts", "surface-contracts.test.ts", "contracts-types.test.ts")),
            ("typescript-engine", "typescript-engine", (
                "engine/connection.test.ts", "engine/host-ownership.test.ts")),
        ):
            commands.append((name, [*node, *[str(package / "test" / file) for file in files]],
                             suite, package))
        commands.append(("typescript-replacement", [sys.executable, "-m", "conformance.check_replacement"],
                         "typescript-replacement", ROOT))
    if installed:
        base = "tests/e2e/installed"
        for binding in ("python", "typescript"):
            for module, families in (
                ("runtime", ("production_runtime", "provider_failure_and_cancellation",
                             "durable_restart_discards_every_process")),
                ("contracts", ("streaming_request_overrides", "unknown_host_key_remedy")),
            ):
                for family in families:
                    commands.append((f"installed-{binding}-{family}", [*pytest,
                                     f"{base}/{binding}/test_{module}.py::test_installed_{family}"],
                                     f"installed-{binding}", ROOT))
            for mode in ("restart", "age", "size", "tool"):
                commands.append((f"installed-{binding}-reasoning-{mode}", [*pytest,
                                 f"{base}/{binding}/test_contracts.py::test_installed_native_reasoning_replay",
                                 "-k", mode], f"installed-{binding}", ROOT))
        for module, families in (
            ("service", ("standalone_http_face", "http_builtin_policy_and_isolation")),
            ("reasoning", ("http_reasoning_has_no_cross_request_state", "http_required_tool_reasoning")),
        ):
            for family in families:
                commands.append((f"installed-{family}", [*pytest,
                                 f"{base}/http/test_{module}.py::test_installed_{family}"],
                                 "installed-http", ROOT))
        commands.append(("installed-interop-restart", [*pytest,
                         f"{base}/interop/test_restart.py::test_installed_durable_restart_discards_every_process"],
                         "installed-interop", ROOT))
    evidence, failed, setup_failed, observations = [], [], [], []
    artifacts = {}
    for name, command, suite, cwd in commands:
        returncode = None
        print(f"Checking {name}", file=sys.stderr, flush=True)
        try:
            if suite and suite.startswith("typescript") and any(
                item in failed + setup_failed for item in ("typescript-types", "typescript-build")
            ):
                raise ValueError("TypeScript prerequisites failed; repair the build before runtime verification")
            if name == "typescript-runtime":
                from conformance.artifacts import runtime_manifest

                runtime = ROOT / "packages/typescript/runtime/linux-x64"
                if "fixture-build" in failed + setup_failed:
                    raise ValueError("Fixture build failed; rebuild before runtime verification")
                artifacts["fixture_runtime"] = runtime_manifest(runtime, ROOT, "fixture")
            is_installed = bool(suite and suite.startswith("installed-"))
            if is_installed and "installed" not in artifacts:
                from conformance.artifacts import inspect_installed

                artifacts["installed"] = inspect_installed(ROOT, execute)
            with tempfile.TemporaryDirectory(prefix="agent-conformance-") as directory:
                cases_path = Path(directory) / "cases.xml"
                if suite in {"typescript", "typescript-engine"}:
                    command = [*command[:5], f"--test-reporter-destination={cases_path}", *command[5:]]
                elif suite == "typescript-replacement":
                    command = [*command, "--report", str(cases_path)]
                elif suite:
                    command = [*command, f"--junitxml={cases_path}"]
                completed = execute(
                    command, cwd=cwd,
                    timeout=120 if name in {"fixture-build", "typescript-runtime"} else 60,
                    env=execution_environment(installed=is_installed),
                )
                returncode = completed.returncode
                if returncode == 0:
                    status = "passed"
                elif command[1:3] == ["-m", "pytest"] and returncode in {2, 4, 5}:
                    status = "setup_failed"
                else:
                    status = "failed"
                output = (completed.stdout + completed.stderr).strip()
                if suite:
                    observations.extend(typescript_cases(cases_path, suite) if suite.startswith("typescript")
                                        else junit_cases(cases_path, suite))
        except subprocess.TimeoutExpired as error:
            status, output = "failed", f"{name} exceeded its execution deadline: {error}"
        except (OSError, ValueError, ET.ParseError) as error:
            status, output = "setup_failed", str(error)
        evidence.append({"name": name, "status": status, "passed": status == "passed",
                         "returncode": returncode, "output": output})
        if status == "setup_failed":
            setup_failed.append(name)
        elif status == "failed":
            failed.append(name)
    catalog = {item["id"]: item for item in json.loads(
        (ROOT / "conformance/clauses/checks.json").read_text())["checks"]}
    try:
        requirements = read_requirements(ROOT / "conformance/evidence", catalog)
        reviews = read_reviews(ROOT / "conformance/reviews.json", ROOT, catalog)
    except (OSError, ValueError, TypeError) as error:
        setup_failed.append("evidence-registry")
        evidence.append({"name": "evidence-registry", "status": "setup_failed", "passed": False,
                         "returncode": None, "output": str(error)})
        requirements, reviews = [], []
    result = assess(catalog, requirements, observations, reviews)
    failed_reviews = [review for review in reviews if review["status"] == "failed_review"]
    failed.extend(f"{review['check']}:{review['surface']}" for review in failed_reviews)
    if source_snapshot() != source:
        setup_failed.append("source-changed")
        evidence.append({"name": "source-changed", "status": "setup_failed", "passed": False,
                         "returncode": None,
                         "output": "Source changed during verification; rerun against one stable source tree"})
        result["covered_checks"] = []
        for row in result["coverage"]:
            if row["status"] == "passed":
                row["status"] = "source_changed"
    satisfied, uncovered = obligations(ROOT / "conformance/clauses", result["covered_checks"])
    return {
        "started_at": started, "source": source, "artifacts": artifacts, "evidence": evidence,
        **result, "observations": observations,
        "failed": failed, "failed_reviews": failed_reviews, "setup_failed": setup_failed,
        "satisfied": satisfied, "uncovered": uncovered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true",
                        help="Require every obligation, including TypeScript, installed artifacts, and reviews.")
    parser.add_argument("--typescript", action="store_true",
                        help="Run TypeScript type, build, and public runtime checks.")
    parser.add_argument("--installed", action="store_true",
                        help="Run installed-artifact checks with the documented artifact inputs.")
    parser.add_argument("--build-runtime", action="store_true",
                        help="Build the TypeScript fixture runtime from this source before testing it.")
    parser.add_argument("--output", type=Path, help="Write the complete report to this path.")
    args = parser.parse_args()
    result = report(typescript=args.typescript or args.full or args.build_runtime,
                    installed=args.installed or args.full,
                    build_runtime=args.build_runtime)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"failed": result["failed"], "setup_failed": result["setup_failed"],
                          "uncovered": len(result["uncovered"]), "report": str(args.output)}))
    else:
        print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["failed"] or result["setup_failed"]
                     or (args.full and result["uncovered"]) else 0)


if __name__ == "__main__":
    main()
