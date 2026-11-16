"""Smart tools: the default amplifier-smart-tools skill and system prompt section, and the switch that removes them.

agent-interface.v1 section 2 (AgentOptions.smart_tools); host-config.v1 sections 1 and 6. The scripted provider
records each ChatRequest the engine sends, so its system message and tools are what the model receives.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
from typing import Any

from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.prompt import BASE_INSTRUCTIONS
from amplifier_agent_engine._records import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput
import pytest

from tests.support.engine import provision

SKILL = "amplifier-smart-tools"
HEADING = "# Smart tools"
REPOSITORY = "https://github.com/microsoft/amplifier-smart-tools"
ENVIRONMENT = "AMPLIFIER_AGENT_SMART_TOOLS"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def write(settings: dict[str, Any]) -> None:
        path.write_text(json.dumps(settings))

    return write


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Everything here must come from the installed engine; the bundled skill never needs the network.
    resolve = socket.getaddrinfo
    connect = socket.socket.connect

    def local_only(host, *args, **kwargs):
        if host not in LOOPBACK:
            raise OSError(f"network access in a unit test: {host}")
        return resolve(host, *args, **kwargs)

    def loopback_only(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            raise OSError(f"network access in a unit test: {address}")
        return connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)


@pytest.fixture
def work(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def skill_source(root: Path, name: str, description: str, body: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return root


@asynccontextmanager
async def engine(work: Path, **fields: Any) -> AsyncGenerator[Any]:
    fields.setdefault("provider", "anthropic")
    fields.setdefault("model", "claude-sonnet-5")
    fields.setdefault("approvals", "allow")
    agent = await assembly.create_engine(AgentOptions(working_directory=str(work), **fields))
    try:
        yield agent
    finally:
        await agent.close()


async def run(agent: Any) -> list[Any]:
    session = await agent.create_session(SessionOptions(persistence="ephemeral"))
    turn = await session.start_turn(TurnInput([TextPart("Reply")]))
    events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success", events[-1].payload.error
    return events


async def first_request(monkeypatch, work: Path, **fields: Any) -> dict[str, Any]:
    probe = provision(monkeypatch, [{"text": "Done"}])
    async with engine(work, **fields) as agent:
        await run(agent)
    return probe.requests[0]


def system_prompt(request: dict[str, Any]) -> str:
    message = request["messages"][0]
    assert message["role"] == "system"
    return message["content"]


def load_skill(request: dict[str, Any]) -> dict[str, Any] | None:
    return next((tool for tool in request.get("tools") or [] if tool["name"] == "load_skill"), None)


def offered(request: dict[str, Any]) -> list[str]:
    tool = load_skill(request)
    return [] if tool is None else tool["parameters"]["properties"]["name"]["enum"]


def section(prompt: str) -> str:
    start = prompt.index(HEADING)
    end = prompt.find("\n# ", start + len(HEADING))
    return prompt[start:] if end == -1 else prompt[start:end]


def is_on(request: dict[str, Any]) -> bool:
    prompt_has_it = HEADING in system_prompt(request)
    skill_offered = SKILL in offered(request)
    assert prompt_has_it == skill_offered
    return prompt_has_it


def refused(error: AgentError) -> str:
    assert error.code == "invalid_input"
    assert error.details == {"field": "smart_tools"}
    text = f"{error.message} {error.remedy}"
    assert "boolean" in text.lower()
    return text


async def test_absent_everywhere_offers_the_skill_and_the_section_with_no_caller_skills_or_tools(monkeypatch, work):
    request = await first_request(monkeypatch, work, tools=[])
    assert offered(request) == [SKILL]
    tool = load_skill(request)
    assert tool is not None
    assert SKILL in tool["description"]
    prompt = system_prompt(request)
    assert prompt.count(HEADING) == 1
    assert prompt.startswith(BASE_INSTRUCTIONS)
    assert len(BASE_INSTRUCTIONS) <= prompt.index(HEADING) < prompt.index("# Environment")


async def test_the_section_points_to_the_skill_and_the_project(monkeypatch, work):
    text = section(system_prompt(await first_request(monkeypatch, work)))
    assert SKILL in text
    assert REPOSITORY in text


async def test_the_section_follows_caller_instructions(monkeypatch, work):
    prompt = system_prompt(await first_request(monkeypatch, work, instructions="Caller instructions"))
    assert prompt.startswith("Caller instructions")
    assert prompt.count(HEADING) == 1
    assert prompt.index(HEADING) > len("Caller instructions")
    assert "# Environment" not in prompt


async def test_loading_the_default_skill_returns_its_body(monkeypatch, work):
    script = [{"tool": {"name": "load_skill", "arguments": {"name": SKILL}}}, {"text": "Done"}]
    provision(monkeypatch, script)
    async with engine(work, tools=[]) as agent:
        events = await run(agent)
    results = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert [result.outcome for result in results] == ["completed"]
    assert results[0].content
    assert results[0].content.strip()


async def test_caller_skills_are_offered_beside_it(monkeypatch, tmp_path, work):
    skills = skill_source(tmp_path / "skills", "review", "Review the supplied work.", "Review.")
    request = await first_request(monkeypatch, work, skills=[str(skills)])
    assert sorted(offered(request)) == sorted([SKILL, "review"])
    tool = load_skill(request)
    assert tool is not None
    assert "Review the supplied work." in tool["description"]


@pytest.mark.parametrize("layer", ["environment", "file"])
async def test_ambient_skills_never_turn_it_off(monkeypatch, tmp_path, work, host, layer):
    skills = str(skill_source(tmp_path / "skills", "review", "Review the supplied work.", "Review."))
    if layer == "environment":
        monkeypatch.setenv("AMPLIFIER_AGENT_SKILLS", skills)
    else:
        host({"skills": [skills]})
    request = await first_request(monkeypatch, work)
    assert sorted(offered(request)) == sorted([SKILL, "review"])
    assert is_on(request)


async def test_a_caller_skill_of_that_name_takes_its_place(monkeypatch, tmp_path, work):
    skills = skill_source(tmp_path / "skills", SKILL, "The caller's own smart tools guidance.", "Caller body.")
    script = [{"tool": {"name": "load_skill", "arguments": {"name": SKILL}}}, {"text": "Done"}]
    probe = provision(monkeypatch, script)
    async with engine(work, skills=[str(skills)]) as agent:
        events = await run(agent)
    request = probe.requests[0]
    assert offered(request) == [SKILL]
    assert is_on(request)
    tool = load_skill(request)
    assert tool is not None
    assert "The caller's own smart tools guidance." in tool["description"]
    results = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert [result.outcome for result in results] == ["completed"]
    assert results[0].content is not None
    assert results[0].content.strip() == "Caller body."


async def test_false_without_caller_skills_offers_nothing_about_smart_tools(monkeypatch, work):
    request = await first_request(monkeypatch, work, tools=[], smart_tools=False)
    assert load_skill(request) is None
    assert HEADING not in system_prompt(request)
    assert SKILL not in json.dumps(request)


async def test_false_leaves_caller_skills_unchanged(monkeypatch, tmp_path, work):
    skills = skill_source(tmp_path / "skills", "review", "Review the supplied work.", "Review.")
    request = await first_request(monkeypatch, work, skills=[str(skills)], smart_tools=False)
    assert offered(request) == ["review"]
    assert SKILL not in json.dumps(request["tools"])
    assert HEADING not in system_prompt(request)


async def test_true_offers_the_skill_and_the_section(monkeypatch, work):
    assert is_on(await first_request(monkeypatch, work, smart_tools=True))


async def test_the_prompt_and_tools_are_byte_identical_across_sessions_and_agents(monkeypatch, work):
    probe = provision(monkeypatch, [{"text": "Done"} for _ in range(4)])
    for _ in range(2):
        async with engine(work) as agent:
            await run(agent)
            await run(agent)
    assert len(probe.requests) == 4
    assert len({system_prompt(request) for request in probe.requests}) == 1
    assert len({json.dumps(request["tools"], sort_keys=True) for request in probe.requests}) == 1
    assert is_on(probe.requests[0])


async def test_the_section_does_not_change_with_the_skills(monkeypatch, tmp_path, work):
    skills = skill_source(tmp_path / "skills", "review", "Review the supplied work.", "Review.")
    without = section(system_prompt(await first_request(monkeypatch, work)))
    beside = section(system_prompt(await first_request(monkeypatch, work, skills=[str(skills)])))
    assert without == beside


@pytest.mark.parametrize(
    ("environment", "file", "expected"),
    [
        ("false", None, False),
        ("0", None, False),
        ("no", None, False),
        (None, False, False),
        (None, "false", False),
        (None, "0", False),
        (None, "no", False),
        (None, True, True),
        ("false", True, False),
    ],
)
async def test_ambient_configuration_fills_an_absent_option(monkeypatch, work, host, environment, file, expected):
    if environment is not None:
        monkeypatch.setenv(ENVIRONMENT, environment)
    if file is not None:
        host({"smart_tools": file})
    assert is_on(await first_request(monkeypatch, work)) is expected


@pytest.mark.parametrize(
    ("option", "environment", "file"),
    [(True, "false", None), (True, None, False), (False, None, True), (True, "maybe", "off"), (False, None, "off")],
)
async def test_the_option_wins_and_shadowed_values_are_not_read(monkeypatch, work, host, option, environment, file):
    if environment is not None:
        monkeypatch.setenv(ENVIRONMENT, environment)
    if file is not None:
        host({"smart_tools": file})
    assert is_on(await first_request(monkeypatch, work, smart_tools=option)) is option


async def test_an_environment_value_shadows_an_invalid_file_value(monkeypatch, work, host):
    monkeypatch.setenv(ENVIRONMENT, "false")
    host({"smart_tools": "off"})
    assert is_on(await first_request(monkeypatch, work)) is False


@pytest.mark.parametrize("value", ["False", "off", "", " no", "maybe"])
async def test_an_invalid_environment_value_is_refused_by_name(monkeypatch, work, value):
    probe = provision(monkeypatch, [{"text": "Done"}])
    monkeypatch.setenv(ENVIRONMENT, value)
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions(working_directory=str(work)))
    assert ENVIRONMENT in refused(caught.value)
    assert probe.requests == []


@pytest.mark.parametrize("value", ["off", "False", None, [], {"enabled": False}])
async def test_an_invalid_file_value_is_refused_by_name(monkeypatch, work, host, value):
    probe = provision(monkeypatch, [{"text": "Done"}])
    host({"smart_tools": value})
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions(working_directory=str(work)))
    assert "smart_tools" in refused(caught.value)
    assert probe.requests == []


async def test_misspelled_names_get_the_smart_tools_key(monkeypatch, work, host):
    host({"smart_tool": False})
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions(working_directory=str(work)))
    assert caught.value.remedy == "Use smart_tools."
    host({})
    monkeypatch.setenv("AMPLIFIER_AGENT_SMART_TOOL", "false")
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions(working_directory=str(work)))
    assert caught.value.remedy == f"Use {ENVIRONMENT}."
