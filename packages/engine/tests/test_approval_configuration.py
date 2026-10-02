import json
import os

from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._records import AgentError, AgentOptions, ApprovalResponse
import pytest


@pytest.fixture
def host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))

    def write(settings):
        path.write_text(json.dumps(settings))

    return write


async def handler(request):
    return ApprovalResponse("allow")


def test_absent_everywhere_leaves_no_policy(host):
    assert resolve(AgentOptions()).approvals is None


@pytest.mark.parametrize("policy", ["allow", "deny"])
def test_environment_sets_static_policy(host, monkeypatch, policy):
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVALS", policy)
    assert resolve(AgentOptions()).approvals == policy


@pytest.mark.parametrize("policy", ["allow", "deny"])
def test_file_sets_static_policy(host, policy):
    host({"approvals": policy})
    assert resolve(AgentOptions()).approvals == policy


def test_environment_wins_over_file(host, monkeypatch):
    host({"approvals": "allow"})
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVALS", "deny")
    assert resolve(AgentOptions()).approvals == "deny"


@pytest.mark.parametrize("value", ["deny", handler])
def test_agent_options_win_over_ambient_policy(host, monkeypatch, value):
    host({"approvals": "allow"})
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVALS", "allow")
    assert resolve(AgentOptions(approvals=value)).approvals is value


@pytest.mark.parametrize("value", ["Allow", " deny", "yes", ""])
def test_invalid_environment_policy_is_refused(host, monkeypatch, value):
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVALS", value)
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.code == "invalid_input"
    assert caught.value.details == {"field": "approvals"}
    assert caught.value.remedy == "Set approvals to 'allow' or 'deny'."


@pytest.mark.parametrize("value", ["ALLOW", True, None, {"command": "ask"}])
def test_invalid_file_policy_is_refused(host, value):
    host({"approvals": value})
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.details == {"field": "approvals"}
    assert caught.value.remedy == "Set approvals to 'allow' or 'deny'."


def test_misspelled_environment_name_gets_nearest_key(host, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVAL", "allow")
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.details == {"field": "AMPLIFIER_AGENT_APPROVAL"}
    assert caught.value.remedy == "Use AMPLIFIER_AGENT_APPROVALS."


def test_misspelled_file_key_gets_nearest_key(host):
    host({"aprovals": "allow"})
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.remedy == "Use approvals."
