"""The face's refusal of an agent with tools and no approval policy counts the default amplifier-smart-tools skill."""

import os

from amplifier_agent import AgentError, AgentOptions
from amplifier_agent_http._app import refuse_unapproved_tools
import pytest


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)


def options(**fields):
    return AgentOptions(**fields)


def test_the_default_skill_alone_is_refused_with_a_remedy_that_turns_it_off():
    with pytest.raises(AgentError) as caught:
        refuse_unapproved_tools(options(tools=[]))
    assert caught.value.code == "approval_unavailable"
    assert "AMPLIFIER_AGENT_SMART_TOOLS" in caught.value.remedy
    assert "AMPLIFIER_AGENT_APPROVALS" in caught.value.remedy


def test_no_tools_and_smart_tools_off_needs_no_policy():
    refuse_unapproved_tools(options(tools=[], smart_tools=False))
