import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conformance import run
from conformance.coverage import assess, matches, obligations, read_requirements
from conformance.reviews import read_reviews


@pytest.fixture(autouse=True)
def isolate_registered_reviews(monkeypatch):
    monkeypatch.setattr(run, "read_reviews", lambda *args: [])


@pytest.mark.parametrize("returncode,status", [
    (0, "passed"), (1, "failed"), (2, "setup_failed"), (4, "setup_failed"),
    (5, "setup_failed"), (None, "setup_failed"),
])
def test_report_distinguishes_setup_from_assertion_failures(monkeypatch, returncode, status):
    def execute(command, **kwargs):
        selected = "packages/python/tests" in command
        result = returncode if selected else 0
        if selected and returncode is None:
            raise FileNotFoundError("Executable is missing")
        xml = next((item.split("=", 1)[1] for item in command if item.startswith("--junitxml=")), None)
        if xml:
            Path(xml).write_text('<testsuite><testcase classname="example" name="present"/></testsuite>')
        return subprocess.CompletedProcess(command, result, "", "")

    monkeypatch.setattr(run, "execute", execute)
    monkeypatch.setattr(run, "read_requirements", lambda *args: [])
    result = run.report()
    evidence = next(item for item in result["evidence"] if item["name"] == "python-binding")
    assert evidence["status"] == status
    assert evidence["returncode"] == returncode
    assert result["failed"] == (["python-binding"] if status == "failed" else [])
    assert result["setup_failed"] == (["python-binding"] if status == "setup_failed" else [])


@pytest.mark.parametrize("full,result,exit_code", [
    (False, {"failed": [], "setup_failed": ["missing-runner"], "uncovered": []}, 1),
    (False, {"failed": ["assertion"], "setup_failed": [], "uncovered": []}, 1),
    (True, {"failed": [], "setup_failed": [], "uncovered": [{"obligation": "AI-001"}]}, 1),
    (False, {"failed": [], "setup_failed": [], "uncovered": [{"obligation": "AI-001"}]}, 0),
    (True, {"failed": [], "setup_failed": [], "uncovered": []}, 0),
])
def test_exit_policy_and_report_parent(monkeypatch, tmp_path, full, result, exit_code):
    destination = tmp_path / "evidence" / "report.json"
    arguments = []
    monkeypatch.setattr(run, "report", lambda **kwargs: arguments.append(kwargs) or result)
    monkeypatch.setattr(sys, "argv", ["conformance/run.py", "--output", str(destination),
                                     *(["--full"] if full else [])])
    with pytest.raises(SystemExit) as error:
        run.main()
    assert error.value.code == exit_code
    assert json.loads(destination.read_text()) == result
    assert arguments[0]["typescript"] is full
    assert arguments[0]["installed"] is full


@pytest.mark.parametrize("child,status", [
    ("", "passed"), ("<failure/>", "failed"),
    ("<error/>", "setup_failed"), ("<skipped/>", "skipped"),
])
def test_junit_preserves_each_case_identity_and_actual_outcome(tmp_path, child, status):
    path = tmp_path / "cases.xml"
    path.write_text(
        '<testsuite><testcase classname="tests.e2e.python.test_sessions" '
        'name="test_shared_session_scenarios[durable-empty]">'
        f'{child}</testcase><testcase classname="unrelated" name="passing"/></testsuite>'
    )
    evidence = run.junit_cases(path, "python-replacement")
    assert evidence == [
        {"suite": "python-replacement", "case": "tests.e2e.python.test_sessions::"
         "test_shared_session_scenarios[durable-empty]", "status": status},
        {"suite": "python-replacement", "case": "unrelated::passing", "status": "passed"},
    ]


def test_collector_keeps_public_surfaces_engine_and_repository_results_separate(monkeypatch):
    seen = []

    def execute(command, **kwargs):
        xml = next((item.split("=", 1)[1] for item in command if item.startswith("--junitxml=")), None)
        if xml:
            selection = command[4]
            seen.append(selection)
            Path(xml).write_text(f'<testsuite><testcase classname="{selection}" name="proof"/></testsuite>')
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run, "execute", execute)
    monkeypatch.setattr(run, "read_requirements", lambda *args: [])
    result = run.report()
    assert {"packages/python/tests", "packages/engine/tests", "packages/http/tests",
            "tests/e2e/python", "tests/e2e/http", "conformance/tests"} <= set(seen)
    assert {"python", "python-replacement", "http", "http-replacement", "engine", "repository"} <= {
        item["suite"] for item in result["observations"]
    }
    assert len(result["observations"]) == len(seen)
    assert result["covered_checks"] == []


def test_typescript_engine_cases_cannot_count_as_binding_or_replacement_evidence(monkeypatch):
    from conformance import artifacts

    def execute(command, **kwargs):
        xml = next((item.split("=", 1)[1] for item in command if item.startswith("--junitxml=")), None)
        report = next((item.split("=", 1)[1] for item in command
                       if item.startswith("--test-reporter-destination=")), None)
        if "--report" in command:
            report = command[command.index("--report") + 1]
        if xml:
            Path(xml).write_text('<testsuite><testcase classname="example" name="proof"/></testsuite>')
        if report:
            case = "replacement" if "--report" in command else (
                "engine" if any("/test/engine/" in item for item in command) else "binding"
            )
            Path(report).write_text("\n".join(json.dumps(record) for record in [
                {"kind": "case", "case": case, "status": "passed"},
                {"kind": "summary", "success": True, "counts": {"tests": 1}},
            ]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run, "execute", execute)
    monkeypatch.setattr(run, "read_requirements", lambda *args: [])
    monkeypatch.setattr(artifacts, "runtime_manifest", lambda *args: {"verified": True})
    result = run.report(typescript=True)
    assert result["failed"] == result["setup_failed"] == []
    assert {(item["suite"], item["case"]) for item in result["observations"]
            if item["suite"].startswith("typescript")} == {
        ("typescript", "binding"), ("typescript-engine", "engine"),
        ("typescript-replacement", "replacement"),
    }


def test_artifact_inputs_reach_only_installed_children(monkeypatch):
    from conformance import artifacts

    inputs = {name: f"/consumer/{name.lower()}" for name in artifacts.INPUTS}
    for name, value in inputs.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AMPLIFIER_AGENT_WORKSPACE", "isolated-host")
    monkeypatch.setenv("AMPLIFIER_AGENT_UNREGISTERED", "must-remain-invalid")
    observed = []
    real_execute = run.execute

    def execute(command, *, cwd, timeout, env):
        child = real_execute([
            sys.executable, "-c",
            "import json, os; print(json.dumps({k: v for k, v in os.environ.items() "
            "if k.startswith('AMPLIFIER_AGENT_')}))",
        ], cwd=cwd, timeout=timeout, env=env)
        assert child.returncode == 0
        environment = json.loads(child.stdout)
        is_installed = any("::test_installed_" in item for item in command)
        assert {name: environment[name] for name in inputs if name in environment} == (
            inputs if is_installed else {}
        )
        assert environment["AMPLIFIER_AGENT_WORKSPACE"] == "isolated-host"
        assert environment["AMPLIFIER_AGENT_UNREGISTERED"] == "must-remain-invalid"
        observed.append(is_installed)
        xml = next((item.split("=", 1)[1] for item in command if item.startswith("--junitxml=")), None)
        if xml:
            Path(xml).write_text('<testsuite><testcase classname="example" name="present"/></testsuite>')
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run, "execute", execute)
    monkeypatch.setattr(run, "read_requirements", lambda *args: [])
    monkeypatch.setattr(artifacts, "inspect_installed", lambda *args: {"verified": True})
    result = run.report(installed=True)
    assert result["failed"] == result["setup_failed"] == []
    assert any(observed) and not all(observed)
    assert {item["suite"] for item in result["observations"]
            if item["suite"].startswith("installed-")} == {
        "installed-python", "installed-typescript", "installed-http", "installed-interop",
    }
    assert all(run.os.environ[name] == value for name, value in inputs.items())


def test_successful_command_without_case_results_is_setup_failure(monkeypatch):
    monkeypatch.setattr(run, "execute", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, "tests passed", ""))
    monkeypatch.setattr(run, "read_requirements", lambda *args: [])
    result = run.report()
    assert "python-binding" in result["setup_failed"]
    assert "http-e2e" in result["setup_failed"]
    assert result["covered_checks"] == []


CATALOG = {"runtime.example": {"kind": "runtime", "surfaces": ["python", "typescript"]}}
REQUIREMENTS = [
    {"check": "runtime.example", "surface": surface,
     "cases": [{"suite": surface, "pattern": "module::test[case-*]", "count": 2}],
     "discrimination": "Reject omitted fields and incorrect values."}
    for surface in ("python", "typescript")
]
OBSERVATIONS = [
    {"suite": surface, "case": f"module::test[case-{case}]", "status": "passed"}
    for surface in ("python", "typescript") for case in (1, 2)
]


def write_registry(directory, groups, requirements):
    (directory / "groups.json").write_text(json.dumps({"groups": groups}))
    (directory / "proof.json").write_text(json.dumps({"requirements": requirements}))


def test_shared_case_groups_expand_without_coupling_their_requirements(tmp_path):
    catalog = {**CATALOG, "runtime.other": {"kind": "runtime", "surfaces": ["python"]}}
    requirements = [
        {**item, "cases": [item["surface"]]} for item in REQUIREMENTS
    ]
    requirements.append({**requirements[0], "check": "runtime.other"})
    groups = {item["surface"]: item["cases"][0] for item in REQUIREMENTS}
    write_registry(tmp_path, groups, requirements)
    expanded = read_requirements(tmp_path, catalog)
    assert expanded == [*REQUIREMENTS, {**REQUIREMENTS[0], "check": "runtime.other"}]
    assert assess(catalog, expanded, OBSERVATIONS)["covered_checks"] == [
        "runtime.example", "runtime.other",
    ]
    expanded[0]["cases"][0]["count"] = 99
    assert expanded[2]["cases"][0]["count"] == 2


def test_complete_evidence_requires_every_case_on_every_surface():
    assert assess(CATALOG, REQUIREMENTS, OBSERVATIONS)["covered_checks"] == ["runtime.example"]
    assert matches("module::test[case-*]", "module::test[case-1]")
    assert not matches("module::test[case-1]", "module::testc")
    assert not assess(CATALOG, REQUIREMENTS[:1], OBSERVATIONS)["covered_checks"]
    assert not assess(CATALOG, REQUIREMENTS, OBSERVATIONS[:2])["covered_checks"]
    assert not assess(CATALOG, REQUIREMENTS, OBSERVATIONS + OBSERVATIONS[:1])["covered_checks"]
    for status in ("failed", "setup_failed", "skipped"):
        broken = copy.deepcopy(OBSERVATIONS)
        broken[-1]["status"] = status
        assert not assess(CATALOG, REQUIREMENTS, broken)["covered_checks"]
    assert not assess(CATALOG, REQUIREMENTS, OBSERVATIONS[:-1])["covered_checks"]


def test_obligation_uses_all_required_checks(tmp_path):
    (tmp_path / "contract.json").write_text(json.dumps({"contract": "example/1", "obligations": [
        {"id": "EX-001", "checks": ["runtime.first", "runtime.second"]},
    ]}))
    satisfied, uncovered = obligations(tmp_path, ["runtime.first"])
    assert satisfied == []
    assert uncovered[0]["checks"] == ["runtime.second"]
    assert obligations(tmp_path, ["runtime.first", "runtime.second"])[1] == []


@pytest.mark.parametrize("mutation", [
    "unknown_check", "unknown_surface", "duplicate", "no_count", "no_cases",
    "no_discrimination", "review", "unknown_group", "repeated_group",
    "malformed_selector", "invalid_count",
])
def test_evidence_registry_rejects_unverifiable_registration(tmp_path, mutation):
    requirement = copy.deepcopy(REQUIREMENTS[0])
    groups = {"python": requirement["cases"][0]}
    requirement["cases"] = ["python"]
    items = [requirement]
    catalog = copy.deepcopy(CATALOG)
    write_registry(tmp_path, groups, items)
    assert read_requirements(tmp_path, catalog) == [REQUIREMENTS[0]]
    if mutation == "unknown_check":
        requirement["check"] = "runtime.missing"
    elif mutation == "unknown_surface":
        requirement["surface"] = "http"
    elif mutation == "duplicate":
        items.append(copy.deepcopy(requirement))
    elif mutation == "no_count":
        groups["python"].pop("count")
    elif mutation == "no_cases":
        requirement["cases"] = []
    elif mutation == "no_discrimination":
        requirement["discrimination"] = ""
    elif mutation == "review":
        catalog["runtime.example"]["kind"] = "review"
    elif mutation == "unknown_group":
        requirement["cases"] = ["missing"]
    elif mutation == "repeated_group":
        requirement["cases"] *= 2
    elif mutation == "malformed_selector":
        groups["python"]["unexpected"] = "not a selector field"
    elif mutation == "invalid_count":
        groups["python"]["count"] = True
    write_registry(tmp_path, groups, items)
    with pytest.raises(ValueError):
        read_requirements(tmp_path, catalog)


def test_review_must_be_current_and_does_not_infer_missing_approval(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("public_api = True\n")
    catalog = {"review.example": {"kind": "review", "surfaces": ["repository"]}}
    path = tmp_path / "reviews.json"
    assert read_reviews(path, tmp_path, catalog) == []
    assert assess(catalog, [], [], [])["coverage"][0]["status"] == "missing_review"
    path.write_text(json.dumps({"reviews": [{
        "check": "review.example", "surfaces": ["repository"], "reviewer": "Fixture reviewer",
        "conclusion": "pass", "rationale": "The public export matches the declared interface.",
        "sources": {"source.py": hashlib.sha256(source.read_bytes()).hexdigest()},
    }]}))
    reviews = read_reviews(path, tmp_path, catalog)
    assert assess(catalog, [], [], reviews)["covered_checks"] == ["review.example"]
    source.write_text("private_api = True\n")
    stale = read_reviews(path, tmp_path, catalog)
    assert stale[0]["status"] == "stale_review"
    assert assess(catalog, [], [], stale)["covered_checks"] == []


@pytest.mark.parametrize("status", ["passed", "failed", "skipped"])
@pytest.mark.parametrize("suite", ["typescript", "typescript-replacement"])
def test_typescript_records_preserve_individual_outcomes(tmp_path, status, suite):
    path = tmp_path / "typescript.jsonl"
    records = [
        {"kind": "case", "case": "contract: exact integers", "status": status},
        {"kind": "summary", "success": status != "failed", "counts": {"tests": 1}},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records))
    cases = run.typescript_cases(path, suite)
    assert cases[0]["suite"] == suite
    assert cases[0]["case"] == "contract: exact integers"
    assert cases[0]["status"] == status


@pytest.mark.parametrize("records", [
    [], [{"kind": "summary", "success": True}],
    [{"kind": "case", "case": "lost result", "status": "passed"}],
    [{"kind": "case", "case": "misreported", "status": "passed"},
     {"kind": "summary", "success": False}],
    [{"kind": "case", "case": "missing outcome"}, {"kind": "summary", "success": True}],
])
def test_typescript_incomplete_or_contradictory_reports_fail_setup(tmp_path, records):
    path = tmp_path / "typescript.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records))
    with pytest.raises(ValueError):
        run.typescript_cases(path)


def test_changed_source_cannot_claim_a_stable_verification_run(monkeypatch):
    snapshots = iter([{"source.py": "before"}, {"source.py": "after"}])
    monkeypatch.setattr(run, "source_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(run, "execute", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(run, "read_requirements", lambda *args: [])
    result = run.report()
    assert "source-changed" in result["setup_failed"]
