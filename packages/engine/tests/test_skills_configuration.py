import json
import os

from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._records import AgentError, AgentOptions
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


def test_absent_everywhere_leaves_no_skills(host):
    assert resolve(AgentOptions()).skills == ()


def test_environment_entries_are_split_on_commas_and_trimmed(host, monkeypatch):
    monkeypatch.setenv(
        "AMPLIFIER_AGENT_SKILLS", " ./skills , git+https://host/owner/repository@main#subdirectory=skills"
    )
    assert resolve(AgentOptions()).skills == ("./skills", "git+https://host/owner/repository@main#subdirectory=skills")


@pytest.mark.parametrize("value", ["", "./skills,", "./skills,,./more", " , ./skills"])
def test_an_empty_environment_entry_is_refused(host, monkeypatch, value):
    monkeypatch.setenv("AMPLIFIER_AGENT_SKILLS", value)
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.code == "invalid_input"
    assert caught.value.details == {"field": "skills"}
    assert "AMPLIFIER_AGENT_SKILLS" in caught.value.remedy


def test_file_sets_skills_and_environment_wins(host, monkeypatch):
    host({"skills": ["./skills", "./more"]})
    assert resolve(AgentOptions()).skills == ("./skills", "./more")
    monkeypatch.setenv("AMPLIFIER_AGENT_SKILLS", "./other")
    assert resolve(AgentOptions()).skills == ("./other",)


@pytest.mark.parametrize("value", ["./skills", None, [""], ["./skills", 3], {"path": "./skills"}])
def test_invalid_file_skills_are_refused(host, value):
    host({"skills": value})
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.details == {"field": "skills"}
    assert caught.value.remedy.startswith("Set skills to a JSON array of nonempty strings")


@pytest.mark.parametrize("value", [[], ["./caller"]])
def test_agent_options_win_over_ambient_skills(host, monkeypatch, value):
    host({"skills": ["./file"]})
    monkeypatch.setenv("AMPLIFIER_AGENT_SKILLS", "./environment")
    assert resolve(AgentOptions(skills=value)).skills == tuple(value)


def test_misspelled_names_get_the_skills_key(host, monkeypatch):
    host({"skill": ["./skills"]})
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.remedy == "Use skills."
    host({})
    monkeypatch.setenv("AMPLIFIER_AGENT_SKILL", "./skills")
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.remedy == "Use AMPLIFIER_AGENT_SKILLS."
