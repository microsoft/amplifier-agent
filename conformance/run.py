"""Report executable evidence separately from uncovered contract obligations."""

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def case_evidence(path: Path) -> list[dict]:
    """Attribute individual observations without promoting partial clause coverage."""
    if not path.exists():
        return []
    registry = json.loads((ROOT / "conformance/cases.json").read_text())
    evidence = []
    for case in ET.parse(path).iter("testcase"):
        module, name = case.get("classname", ""), case.get("name", "")
        for surface, modules in registry.items():
            checks = modules.get(module, {}).get(name.split("[", 1)[0])
            if checks:
                status = "failed" if case.find("failure") is not None else (
                    "setup_failed" if case.find("error") is not None else (
                        "skipped" if case.find("skipped") is not None else "passed"
                    )
                )
                evidence.append({
                    "surface": surface, "case": f"{module}::{name}",
                    "checks": checks, "status": status,
                })
    return evidence


def report() -> dict:
    pytest = [sys.executable, "-m", "pytest", "-q"]
    commands = [
        ("inventory", [sys.executable, "conformance/check_inventory.py"], []),
        ("http-fixtures", [sys.executable, "conformance/http/check.py"], []),
        ("python-binding", [*pytest, "packages/python/tests"], []),
        ("engine", [*pytest, "packages/engine/tests"], []),
        ("integration", [*pytest, "tests/integration"], []),
        ("conformance", [*pytest, "conformance/tests"], []),
        ("local-forge-unit", [*pytest, "tests/e2e/test_local_forge.py"], []),
        (
            "http-runtime",
            [*pytest, "packages/http/tests"],
            [
                "runtime.http.auth",
                "runtime.http.bind",
                "runtime.http.unknown_model",
            ],
        ),
    ]
    evidence, failed, setup_failed, covered = [], [], [], set()
    observations = []
    for name, command, checks in commands:
        try:
            with tempfile.TemporaryDirectory(prefix="agent-conformance-") as directory:
                cases_path = Path(directory) / "cases.xml"
                if name == "integration":
                    command = [*command, f"--junitxml={cases_path}"]
                completed = subprocess.run(
                    command, cwd=ROOT, capture_output=True, text=True, timeout=60
                )
                if name == "integration":
                    observations.extend(case_evidence(cases_path))
            returncode = completed.returncode
            if returncode == 0:
                status = "passed"
            elif command[1:3] == ["-m", "pytest"] and returncode in {2, 4, 5}:
                status = "setup_failed"
            else:
                status = "failed"
            output = completed.stdout + completed.stderr
        except subprocess.TimeoutExpired as error:
            status, returncode, output = "failed", None, str(error)
        except FileNotFoundError as error:
            status, returncode, output = "setup_failed", None, str(error)
        evidence.append(
            {
                "name": name,
                "status": status,
                "passed": status == "passed",
                "returncode": returncode,
                "output": output.strip(),
            }
        )
        if status == "passed":
            covered.update(checks)
        elif status == "setup_failed":
            setup_failed.append(name)
        else:
            failed.append(name)
    uncovered = []
    for path in sorted((ROOT / "conformance/clauses").glob("*.json")):
        document = json.loads(path.read_text())
        for obligation in document.get("obligations", []):
            missing = sorted(set(obligation["checks"]) - covered)
            if missing:
                uncovered.append(
                    {
                        "contract": document["contract"],
                        "obligation": obligation["id"],
                        "checks": missing,
                    }
                )
    return {
        "evidence": evidence,
        "covered_checks": sorted(covered),
        "case_evidence": observations,
        "failed": failed,
        "setup_failed": setup_failed,
        "uncovered": uncovered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full", action="store_true", help="Require evidence for every inventoried obligation."
    )
    parser.add_argument("--output", type=Path, help="Write the complete report to this path.")
    args = parser.parse_args()
    result = report()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "failed": result["failed"],
                    "setup_failed": result["setup_failed"],
                    "uncovered": len(result["uncovered"]),
                    "report": str(args.output),
                }
            )
        )
    else:
        print(json.dumps(result, indent=2))
    raise SystemExit(
        1
        if result["failed"] or result["setup_failed"] or (args.full and result["uncovered"])
        else 0
    )


if __name__ == "__main__":
    main()
