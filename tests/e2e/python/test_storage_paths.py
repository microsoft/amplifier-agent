import os
from pathlib import Path
import subprocess
import sys

import pytest

_STORAGE_LIFECYCLE = """
import asyncio
import json
import os
import sys
from pathlib import Path

from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
from tests.support.scripted_provider import install


async def main():
    base, source = Path(sys.argv[1]), sys.argv[2]
    origin = base / 'construction'
    origin.mkdir()
    home = base / 'home'
    home.mkdir()
    os.environ['HOME'] = str(home)
    os.environ['AMPLIFIER_AGENT_WORKSPACE'] = 'original'
    config = origin / 'settings' / 'host.json'
    config.parent.mkdir()
    os.environ['AMPLIFIER_AGENT_CONFIG'] = 'settings/host.json'
    host = {}
    options = {}
    expected_storage = origin / 'sessions'
    if source in ('option_path', 'option_text'):
        host['storage'] = 'unselected-file'
        os.environ['AMPLIFIER_AGENT_STORAGE'] = 'unselected-env'
        options['storage'] = Path('sessions') if source == 'option_path' else 'sessions'
    elif source == 'environment':
        host['storage'] = 'unselected-file'
        os.environ['AMPLIFIER_AGENT_STORAGE'] = 'sessions'
    elif source == 'file':
        host['storage'] = 'sessions'
    elif source == 'absolute':
        options['storage'] = str(expected_storage)
    elif source == 'tilde':
        options['storage'] = '~/sessions'
        expected_storage = home / 'sessions'
    elif source == 'default':
        expected_storage = home / '.amplifier-agent'
    else:
        raise AssertionError(source)
    config.write_text(json.dumps(host))
    install([{'text': 'Saved reply', 'chunks': ['Saved reply']}])
    os.chdir(origin)
    first = await create_agent(AgentOptions(**options))
    second = None
    other_workspace = None

    def move(name):
        destination = base / name
        destination.mkdir()
        os.chdir(destination)
        return destination

    try:
        before_create = move('before-create')
        os.environ['AMPLIFIER_AGENT_CONFIG'] = str(config)
        second = await create_agent(AgentOptions(storage=expected_storage))
        os.environ['AMPLIFIER_AGENT_STORAGE'] = str(base / 'changed-storage')
        os.environ['AMPLIFIER_AGENT_WORKSPACE'] = 'different'
        os.environ['HOME'] = str(base / 'changed-home')
        other_workspace = await create_agent(AgentOptions(storage=expected_storage))
        session = await first.create_session(SessionOptions(session_id='anchored-session'))
        assert await first.list_sessions() == [session.info]
        assert await second.list_sessions() == [session.info]
        assert await other_workspace.list_sessions() == []

        before_checkpoint = move('before-checkpoint')
        assert (await session.run(TurnInput([TextPart('Original input')]))).state == 'success'
        history = session.history
        await session.close()

        before_resume = move('before-resume')
        resumed = await second.resume_session('anchored-session')
        assert resumed.history == history
        await resumed.close()

        before_delete = move('before-delete')
        await first.delete_session('anchored-session')
        assert await second.list_sessions() == []
        for destination in (before_create, before_checkpoint, before_resume, before_delete):
            assert list(destination.iterdir()) == [], destination
        assert not (origin / 'unselected-file').exists()
        assert not (origin / 'unselected-env').exists()
        assert not (config.parent / 'sessions').exists()
        assert not (base / 'changed-storage').exists()
        assert not (base / 'changed-home').exists()
    finally:
        for agent in (other_workspace, second, first):
            if agent is not None:
                await agent.close()


asyncio.run(main())
"""


@pytest.mark.parametrize(
    "source", ["option_path", "option_text", "environment", "file", "absolute", "tilde", "default"]
)
def test_storage_remains_at_construction_root_after_directory_changes(tmp_path, source):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AMPLIFIER_AGENT_")}
    result = subprocess.run(
        [sys.executable, "-c", _STORAGE_LIFECYCLE, str(tmp_path), source],
        cwd=Path(__file__).parents[3],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
