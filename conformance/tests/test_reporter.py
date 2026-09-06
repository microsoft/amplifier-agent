import json
import subprocess
import sys

import pytest

from conformance import run


@pytest.mark.parametrize(
    "returncode,status",
    [
        (0, "passed"),
        (1, "failed"),
        (2, "setup_failed"),
        (4, "setup_failed"),
        (5, "setup_failed"),
        (None, "setup_failed"),
    ],
)
def test_report_distinguishes_setup_from_assertion_failures(monkeypatch, returncode, status):
    def execute(command, **kwargs):
        if "packages/python/tests" not in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if returncode is None:
            raise FileNotFoundError("Executable is missing")
        return subprocess.CompletedProcess(command, returncode, "", "")

    monkeypatch.setattr(run.subprocess, "run", execute)
    result = run.report()
    evidence = next(item for item in result["evidence"] if item["name"] == "python-binding")
    assert evidence["status"] == status
    assert evidence["returncode"] == returncode
    assert result["failed"] == (["python-binding"] if status == "failed" else [])
    assert result["setup_failed"] == (["python-binding"] if status == "setup_failed" else [])


def test_setup_failure_exits_nonzero_and_creates_report_parent(monkeypatch, tmp_path):
    result = {"failed": [], "setup_failed": ["missing-runner"], "uncovered": []}
    destination = tmp_path / "evidence" / "report.json"
    monkeypatch.setattr(run, "report", lambda: result)
    monkeypatch.setattr(sys, "argv", ["conformance/run.py", "--output", str(destination)])
    with pytest.raises(SystemExit) as error:
        run.main()
    assert error.value.code == 1
    assert json.loads(destination.read_text()) == result


@pytest.mark.parametrize("child,status", [
    ("", "passed"), ("<failure/>", "failed"),
    ("<error/>", "setup_failed"), ("<skipped/>", "skipped"),
])
def test_case_evidence_preserves_surface_case_and_actual_outcome(tmp_path, child, status):
    path = tmp_path / "cases.xml"
    path.write_text(
        '<testsuite><testcase classname="tests.integration.test_sessions" '
        'name="test_shared_session_scenarios[durable-empty]">'
        f'{child}</testcase><testcase classname="unrelated" name="passing"/></testsuite>'
    )
    evidence = run.case_evidence(path)
    assert len(evidence) == 1
    assert evidence[0]["surface"] == "python"
    assert evidence[0]["case"].endswith("[durable-empty]")
    assert evidence[0]["status"] == status
    assert "runtime.session.fork" in evidence[0]["checks"]


def test_case_registry_only_references_declared_checks_and_surfaces():
    registry = json.loads((run.ROOT / "conformance/cases.json").read_text())
    checks = {
        check["id"]: check
        for check in json.loads((run.ROOT / "conformance/clauses/checks.json").read_text())["checks"]
    }
    for surface, modules in registry.items():
        for cases in modules.values():
            for ids in cases.values():
                for identifier in ids:
                    assert surface in checks[identifier]["surfaces"]
