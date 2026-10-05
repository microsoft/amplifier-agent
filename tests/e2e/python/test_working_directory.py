"""The agent's working directory and additional directories, observed through tool effects."""

import asyncio
from pathlib import Path

from amplifier_agent import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
import pytest
import yaml

from tests.support.engine import provision, provision_many


@pytest.fixture(autouse=True)
def separate_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def host(monkeypatch, tmp_path):
    directory = tmp_path / "host"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory.resolve()


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    return directory.resolve()


def call(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


def locate(**arguments):
    """Report where bash runs and where a relative write lands."""
    return [
        call("bash", command="pwd > where"),
        call("write_file", file_path="note.txt", content="relative"),
        {"text": "Done", **arguments},
    ]


def options(**values):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow", **values)


async def run(agent, persistence="ephemeral"):
    async with await agent.create_session(SessionOptions(persistence=persistence)) as session:
        turn = await session.start_turn(TurnInput([TextPart("Perform the requested work.")]))
        return [event async for event in turn.events()]


def landed_in(directory):
    assert (directory / "where").read_text().strip() == str(directory)
    assert (directory / "note.txt").read_text() == "relative"


def results(events):
    return [event.payload.resolution for event in events if event.type == "tool_result"]


def slug(path):
    text = str(path).replace("/", "-").replace("\\", "-").replace(":", "")
    return text if text.startswith("-") else "-" + text


async def test_a_relative_working_directory_resolves_against_the_process_directory(monkeypatch, host):
    (host / "project").mkdir()
    provision(monkeypatch, locate())
    async with await create_agent(options(working_directory="project")) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    landed_in(host / "project")
    assert not (host / "where").exists()


@pytest.mark.parametrize("kind", [str, Path])
async def test_an_absolute_working_directory_is_used_whatever_the_process_directory(monkeypatch, host, project, kind):
    provision(monkeypatch, locate())
    async with await create_agent(options(working_directory=kind(project))) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    landed_in(project)
    assert list(host.iterdir()) == []


async def test_a_symlinked_working_directory_resolves_to_its_target(monkeypatch, host, project, separate_home):
    link = host.parent / "link"
    link.symlink_to(project, target_is_directory=True)
    provision(monkeypatch, locate())
    async with await create_agent(options(working_directory=link)) as agent:
        events = await run(agent, persistence="durable")
    assert events[-1].payload.state == "success"
    landed_in(project)
    projects = separate_home / ".amplifier-agent" / "projects"
    assert [path.name for path in projects.iterdir()] == [slug(project)]


@pytest.mark.parametrize("kind", ["missing", "file"])
async def test_a_working_directory_that_is_not_an_existing_directory_is_refused(monkeypatch, host, kind):
    target = host / "target"
    if kind == "file":
        target.write_text("not a directory")
    probe = provision(monkeypatch, locate())
    with pytest.raises(AgentError) as caught:
        await create_agent(options(working_directory=target))
    assert caught.value.code == "invalid_input"
    assert caught.value.remedy
    assert str(target) in caught.value.message
    assert probe.requests == []


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "working_directory"])
async def test_creating_running_and_closing_an_agent_never_changes_the_process_directory(
    monkeypatch, host, project, explicit
):
    provision(monkeypatch, [call("bash", command="cd / && pwd"), {"text": "Done"}])
    values = {"working_directory": project} if explicit else {}
    agent = await create_agent(options(**values))
    assert Path.cwd() == host
    events = await run(agent)
    assert Path.cwd() == host
    await agent.close()
    assert Path.cwd() == host
    assert events[-1].payload.state == "success"


async def test_a_later_process_directory_change_never_moves_the_agent(monkeypatch, host, project):
    provision(monkeypatch, locate())
    async with await create_agent(options()) as agent:
        monkeypatch.chdir(project)
        events = await run(agent)
    assert events[-1].payload.state == "success"
    landed_in(host)
    assert list(project.iterdir()) == []


async def test_a_later_process_directory_change_never_moves_an_explicit_working_directory(
    monkeypatch, host, project, tmp_path
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (host / "project").mkdir()
    provision(monkeypatch, locate())
    async with await create_agent(options(working_directory="project")) as agent:
        monkeypatch.chdir(elsewhere)
        (elsewhere / "project").mkdir()
        events = await run(agent)
    assert events[-1].payload.state == "success"
    landed_in(host / "project")
    assert list((elsewhere / "project").iterdir()) == []


async def test_two_agents_in_one_process_work_in_their_own_directories(monkeypatch, host, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    provision(monkeypatch, locate())
    async with (
        await create_agent(options(working_directory=first)) as one,
        await create_agent(options(working_directory=second)) as two,
    ):
        outcomes = await asyncio.gather(run(one), run(two))
    assert [events[-1].payload.state for events in outcomes] == ["success", "success"]
    landed_in(first.resolve())
    landed_in(second.resolve())
    assert list(host.iterdir()) == []


async def test_relative_skill_sources_resolve_against_the_working_directory(monkeypatch, host, project):
    for root, body in ((project, "Project review instructions."), (host, "Host review instructions.")):
        skill = root / "skills" / "review"
        skill.mkdir(parents=True)
        header = {"name": "review", "description": "Review the supplied work."}
        (skill / "SKILL.md").write_text("---\n" + yaml.safe_dump(header) + "---\n" + body)
    probe = provision(monkeypatch, [call("load_skill", name="review"), {"text": "Done"}])
    async with await create_agent(options(working_directory=project, skills=["skills"])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    seen = str(probe.requests[-1])
    assert "Project review instructions." in seen
    assert "Host review instructions." not in seen


async def test_delegated_work_inherits_the_working_and_additional_directories(monkeypatch, host, project, tmp_path):
    shared = (tmp_path / "shared").resolve()
    shared.mkdir()
    provision_many(
        monkeypatch,
        [call("delegate", instruction="Record where you work."), {"text": "Parent complete"}],
        [
            *locate()[:-1],
            call("write_file", file_path=str(shared / "child.txt"), content="delegated"),
            {"text": "Child complete"},
        ],
    )
    async with await create_agent(options(working_directory=project, additional_directories=[shared])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    landed_in(project)
    assert (shared / "child.txt").read_text() == "delegated"
    assert list(host.iterdir()) == []


async def test_write_file_outside_the_working_directory_is_refused(monkeypatch, host, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    provision(
        monkeypatch,
        [
            call("write_file", file_path=str(host / "inside.txt"), content="inside"),
            call("write_file", file_path=str(outside / "outside.txt"), content="outside"),
            {"text": "Done"},
        ],
    )
    async with await create_agent(options(tool_error_policy="continue")) as agent:
        events = await run(agent)
    inside, refused = results(events)
    assert inside.outcome == "completed"
    assert refused.outcome != "completed"
    assert (host / "inside.txt").read_text() == "inside"
    assert not (outside / "outside.txt").exists()


async def test_write_file_reaches_additional_directories_and_no_others(monkeypatch, host, project, tmp_path):
    shared, outside = tmp_path / "shared", tmp_path / "outside"
    shared.mkdir()
    outside.mkdir()
    provision(
        monkeypatch,
        [
            call("write_file", file_path=str(shared / "shared.txt"), content="shared"),
            call("write_file", file_path=str(outside / "outside.txt"), content="outside"),
            {"text": "Done"},
        ],
    )
    async with await create_agent(
        options(working_directory=project, additional_directories=[shared], tool_error_policy="continue")
    ) as agent:
        events = await run(agent)
    allowed, refused = results(events)
    assert allowed.outcome == "completed"
    assert refused.outcome != "completed"
    assert (shared / "shared.txt").read_text() == "shared"
    assert not (outside / "outside.txt").exists()


async def test_relative_additional_directories_resolve_against_the_working_directory(monkeypatch, host, tmp_path):
    project = tmp_path / "nested" / "project"
    project.mkdir(parents=True)
    shared = (tmp_path / "nested" / "shared").resolve()
    shared.mkdir()
    provision(
        monkeypatch, [call("write_file", file_path=str(shared / "shared.txt"), content="shared"), {"text": "Done"}]
    )
    async with await create_agent(options(working_directory=project, additional_directories=["../shared"])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert (shared / "shared.txt").read_text() == "shared"


@pytest.mark.parametrize("kind", ["missing", "file"])
async def test_an_additional_directory_that_is_not_an_existing_directory_is_refused(monkeypatch, host, project, kind):
    target = project / "target"
    if kind == "file":
        target.write_text("not a directory")
    probe = provision(monkeypatch, locate())
    with pytest.raises(AgentError) as caught:
        await create_agent(options(working_directory=project, additional_directories=["target"]))
    assert caught.value.code == "invalid_input"
    assert caught.value.remedy
    assert str(target) in caught.value.message
    assert probe.requests == []
