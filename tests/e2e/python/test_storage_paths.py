import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from amplifier_agent import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
import pytest

from tests.support.engine import provision

_SESSIONS_LIFECYCLE = """
import asyncio
import asyncio
import json
import os
import sys
from pathlib import Path

from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
from tests.support.scripted_provider import install


def slug(path):
    text = str(path).replace('/', '-').replace('\\\\', '-').replace(':', '')
    return text if text.startswith('-') else '-' + text


async def main():
    base, source = Path(sys.argv[1]).resolve(), sys.argv[2]
    origin = base / 'construction'
    origin.mkdir()
    project = base / 'project'
    project.mkdir()
    home = base / 'home'
    home.mkdir()
    os.environ['HOME'] = str(home)
    config = origin / 'settings' / 'host.json'
    config.parent.mkdir()
    os.environ['AMPLIFIER_AGENT_CONFIG'] = 'settings/host.json'
    host = {}
    options = {}
    expected = origin / 'sessions'
    if source in ('option_path', 'option_text'):
        host['sessions_directory'] = 'unselected-file'
        os.environ['AMPLIFIER_AGENT_SESSIONS_DIRECTORY'] = 'unselected-env'
        options['sessions_directory'] = Path('sessions') if source == 'option_path' else 'sessions'
    elif source == 'environment':
        host['sessions_directory'] = 'unselected-file'
        os.environ['AMPLIFIER_AGENT_SESSIONS_DIRECTORY'] = 'sessions'
    elif source == 'file':
        host['sessions_directory'] = 'sessions'
    elif source == 'absolute':
        options['sessions_directory'] = str(expected)
    elif source == 'tilde':
        options['sessions_directory'] = '~/sessions'
        expected = home / 'sessions'
    elif source == 'relative_beside_working_directory':
        options['working_directory'] = project
        options['sessions_directory'] = 'sessions'
    elif source == 'default':
        expected = home / '.amplifier-agent' / 'projects' / slug(origin)
    elif source == 'default_from_working_directory':
        options['working_directory'] = project
        expected = home / '.amplifier-agent' / 'projects' / slug(project)
    else:
        raise AssertionError(source)
    config.write_text(json.dumps(host))
    install([{'text': 'Saved reply', 'chunks': ['Saved reply']}])
    os.chdir(origin)
    first = await create_agent(AgentOptions(**options))
    second = None
    other = None

    def move(name):
        destination = base / name
        destination.mkdir()
        os.chdir(destination)
        return destination

    try:
        before_create = move('before-create')
        os.environ['AMPLIFIER_AGENT_CONFIG'] = str(config)
        second = await create_agent(AgentOptions(sessions_directory=expected))
        os.environ['AMPLIFIER_AGENT_SESSIONS_DIRECTORY'] = str(base / 'changed-sessions')
        os.environ['HOME'] = str(base / 'changed-home')
        other = await create_agent(AgentOptions(sessions_directory=base / 'other-sessions'))
        session = await first.create_session(SessionOptions(session_id='anchored-session'))
        assert await first.list_sessions() == [session.info]
        assert await second.list_sessions() == [session.info]
        assert await other.list_sessions() == []

        before_checkpoint = move('before-checkpoint')
        assert (await session.run(TurnInput([TextPart('Original input')]))).state == 'success'
        history = session.history
        await session.close()
        assert (expected / 'sessions' / 'anchored-session' / 'transcript.jsonl').is_file()
        assert (expected / 'sessions' / 'anchored-session' / 'metadata.json').is_file()

        before_resume = move('before-resume')
        resumed = await second.resume_session('anchored-session')
        assert resumed.history == history
        await resumed.close()

        before_delete = move('before-delete')
        await first.delete_session('anchored-session')
        assert await second.list_sessions() == []
        for destination in (before_create, before_checkpoint, before_resume, before_delete, project):
            assert list(destination.iterdir()) == [], destination
        assert not (origin / 'unselected-file').exists()
        assert not (origin / 'unselected-env').exists()
        assert not (config.parent / 'sessions').exists()
        assert not (project / 'sessions').exists()
        assert not (base / 'changed-sessions').exists()
        assert not (base / 'changed-home').exists()
    finally:
        for agent in (other, second, first):
            if agent is not None:
                await agent.close()


asyncio.run(main())
"""


@pytest.fixture(autouse=True)
def separate_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def slug(path):
    text = str(path).replace("/", "-").replace("\\", "-").replace(":", "")
    return text if text.startswith("-") else "-" + text


@pytest.mark.parametrize(
    "source",
    [
        "option_path",
        "option_text",
        "environment",
        "file",
        "absolute",
        "tilde",
        "relative_beside_working_directory",
        "default",
        "default_from_working_directory",
    ],
)
def test_sessions_remain_in_the_construction_sessions_directory_after_directory_changes(tmp_path, source):
    base = tmp_path / "lifecycle"
    base.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AMPLIFIER_AGENT_")}
    result = subprocess.run(
        [sys.executable, "-c", _SESSIONS_LIFECYCLE, str(base), source],
        cwd=Path(__file__).parents[3],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "name",
    ["app", "C:\\projects\\web-app", "with:colons"],
    ids=["plain", "windows_style_name", "colons"],
)
async def test_the_default_sessions_directory_is_named_by_the_working_directory_slug(
    monkeypatch, tmp_path, separate_home, name
):
    project = tmp_path / name
    project.mkdir()
    monkeypatch.chdir(project)
    provision(monkeypatch, [{"text": "Saved reply"}])
    async with (
        await create_agent(AgentOptions()) as agent,
        await agent.create_session(SessionOptions(session_id="slugged-session")) as session,
    ):
        assert (await session.run(TurnInput([TextPart("Save this")]))).state == "success"
    expected = slug(project.resolve())
    assert not {"/", "\\", ":"} & set(expected)
    assert expected.startswith("-")
    if name == "C:\\projects\\web-app":
        assert expected.endswith("-C-projects-web-app")
    projects = separate_home / ".amplifier-agent" / "projects"
    assert [path.name for path in projects.iterdir()] == [expected]
    assert (projects / expected / "sessions" / "slugged-session" / "transcript.jsonl").is_file()
    assert list(project.iterdir()) == []


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("option", "storage"),
        ("file", "storage"),
        ("file", "workspace"),
        ("environment", "AMPLIFIER_AGENT_STORAGE"),
        ("environment", "AMPLIFIER_AGENT_WORKSPACE"),
    ],
)
async def test_storage_and_workspace_are_refused_by_name(monkeypatch, tmp_path, isolated_host, source, name):
    probe = provision(monkeypatch, [{"text": "Unused"}])
    options = AgentOptions()
    if source == "option":
        assert name not in vars(options)
        vars(options)[name] = str(tmp_path / "legacy")
    elif source == "file":
        isolated_host.write_text(json.dumps({name: "legacy"}))
    else:
        monkeypatch.setenv(name, "legacy")
    with pytest.raises(AgentError) as caught:
        await create_agent(options)
    assert caught.value.code == "invalid_input"
    assert caught.value.remedy
    assert name in caught.value.message
    assert probe.requests == []
    assert not (tmp_path / "legacy").exists()


def call(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


def shared_folders(tmp_path):
    first, second = (tmp_path / "notes" / "finance").resolve(), (tmp_path / "notes" / "health").resolve()
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    return first, second, tmp_path / "personal"


async def start(agent, session_id):
    session = await agent.create_session(SessionOptions(session_id=session_id))
    return session, await session.start_turn(TurnInput([TextPart(f"Work for {session_id}")]))


async def finish(turn):
    events = [event async for event in turn.events()]
    return events[-1].payload.state


async def test_agents_with_different_working_directories_share_one_sessions_directory(monkeypatch, tmp_path):
    first, second, sessions = shared_folders(tmp_path)
    provision(monkeypatch, [{"text": "Saved reply"}])
    async with (
        await create_agent(AgentOptions(working_directory=first, sessions_directory=sessions)) as one,
        await create_agent(AgentOptions(working_directory=second, sessions_directory=sessions)) as two,
    ):
        (first_session, first_turn), (second_session, second_turn) = await asyncio.gather(
            start(one, "finance-session"), start(two, "health-session")
        )
        states = await asyncio.gather(finish(first_turn), finish(second_turn))
        assert states == ["success", "success"]
        for agent in (one, two):
            listed = sorted(record.session_id for record in await agent.list_sessions())
            assert listed == ["finance-session", "health-session"]
        await first_session.close()
        await second_session.close()
        for session_id in ("finance-session", "health-session"):
            assert (sessions / "sessions" / session_id / "transcript.jsonl").is_file()
        resumed = await two.resume_session("finance-session")
        assert len(resumed.history) == 1
        assert (await resumed.run(TurnInput([TextPart("Continue")]))).state == "success"
        await resumed.close()
    assert sorted(path.name for path in (sessions / "sessions").iterdir() if path.is_dir()) == [
        "finance-session",
        "health-session",
    ]
    assert list(first.iterdir()) == []
    assert list(second.iterdir()) == []


_SHARED_PROCESS = """
import asyncio
import sys
from pathlib import Path

from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
from tests.support.scripted_provider import install


async def main():
    role, working, sessions = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    install([{'text': 'Saved reply'}])
    async with await create_agent(AgentOptions(working_directory=working, sessions_directory=sessions)) as agent:
        if role == 'create':
            async with await agent.create_session(SessionOptions(session_id='shared-session')) as session:
                assert (await session.run(TurnInput([TextPart('First folder')]))).state == 'success'
        else:
            assert [record.session_id for record in await agent.list_sessions()] == ['shared-session']
            async with await agent.resume_session('shared-session') as session:
                assert len(session.history) == 1
                assert (await session.run(TurnInput([TextPart('Second folder')]))).state == 'success'
                assert len(session.history) == 2


asyncio.run(main())
"""


def test_processes_with_different_working_directories_share_one_sessions_directory(tmp_path, separate_home):
    first, second, sessions = shared_folders(tmp_path)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AMPLIFIER_AGENT_")}
    for role, working in (("create", first), ("resume", second)):
        result = subprocess.run(
            [sys.executable, "-c", _SHARED_PROCESS, role, str(working), str(sessions)],
            cwd=Path(__file__).parents[3],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, role + "\n" + result.stdout + result.stderr
    assert (sessions / "sessions" / "shared-session" / "transcript.jsonl").is_file()
    assert list(first.iterdir()) == []
    assert list(second.iterdir()) == []
    assert list(separate_home.iterdir()) == []


async def test_a_resumed_session_works_in_the_resuming_agents_working_directory(monkeypatch, tmp_path):
    first, second, sessions = shared_folders(tmp_path)
    provision(monkeypatch, [call("bash", command="pwd > where"), {"text": "Done"}])
    async with (
        await create_agent(
            AgentOptions(working_directory=first, sessions_directory=sessions, approvals="allow")
        ) as one,
        await one.create_session(SessionOptions(session_id="moving-session")) as session,
    ):
        assert (await session.run(TurnInput([TextPart("Record the folder")]))).state == "success"
    metadata = sessions / "sessions" / "moving-session" / "metadata.json"
    assert json.loads(metadata.read_text())["working_dir"] == str(first)
    (first / "where").unlink()
    async with (
        await create_agent(
            AgentOptions(working_directory=second, sessions_directory=sessions, approvals="allow")
        ) as two,
        await two.resume_session("moving-session") as resumed,
    ):
        assert (await resumed.run(TurnInput([TextPart("Record the folder again")]))).state == "success"
    assert (second / "where").read_text().strip() == str(second)
    assert list(first.iterdir()) == []
    assert json.loads(metadata.read_text())["working_dir"] == str(second)
