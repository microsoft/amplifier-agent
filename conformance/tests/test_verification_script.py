"""Require successful execution and complete evidence from the verification runner."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def verify():
    path = Path(__file__).parents[2] / "scripts/verify.py"
    spec = importlib.util.spec_from_file_location("verify_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("content", [
    None, "not XML", "<testsuites/>",
    '<testsuite><testcase/><testcase><skipped/></testcase></testsuite>',
    '<testsuite><testcase/><testcase><failure/></testcase></testsuite>',
    '<testsuite><testcase/><testcase><error/></testcase></testsuite>',
])
def test_incomplete_or_unsuccessful_evidence_cannot_pass(verify, tmp_path, content):
    report = tmp_path / "cases.xml"
    if content is not None:
        report.write_text(content)
    assert verify.passed_cases(report) == 0


def test_nonzero_exit_cannot_be_masked_by_passing_report(verify, tmp_path, monkeypatch):
    (tmp_path / "1.xml").write_text("<testsuite><testcase/></testsuite>")
    assert verify.passed_cases(tmp_path / "1.xml") == 1
    monkeypatch.setattr(verify, "GROUPS", [("Failing process", ["example.py"])])
    monkeypatch.setattr(verify, "run", lambda *args: False)
    assert verify.deterministic(tmp_path, {}) is False


def test_isolation_drops_host_settings_credentials_and_test_filters(verify, tmp_path, monkeypatch):
    for name in (
        "AMPLIFIER_AGENT_STORAGE", "AMPLIFIER_AGENT_CONFIG", "AMPLIFIER_AGENT_PROVIDER",
        "AMPLIFIER_AGENT_ENGINE_TEST_SCENARIO", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL",
        "GOOGLE_API_KEY", "PYTEST_ADDOPTS", "PYTHONOPTIMIZE", "PYTHONPATH",
    ):
        monkeypatch.setenv(name, "unrelated-host-value")
    environment = verify.isolated_environment(tmp_path)
    assert "unrelated-host-value" not in environment.values()
    assert environment["AMPLIFIER_AGENT_STORAGE"] == str(tmp_path / "sessions")
    assert Path(environment["AMPLIFIER_AGENT_CONFIG"]).read_text() == "{}\n"


def test_live_process_without_success_report_cannot_pass(verify, tmp_path, monkeypatch):
    def no_report(command, log, environment, *, cwd):
        log.write_text("Process stopped without a result.\n")
        return True

    monkeypatch.setattr(verify, "run", no_report)
    assert verify.live(tmp_path, tmp_path, {}, "openai", "explicit-model") is False
