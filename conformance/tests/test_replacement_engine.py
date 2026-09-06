import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conformance.fixtures import replacement


def test_replacement_durable_state_survives_all_processes_exiting(tmp_path):
    program = """
import asyncio
import json
import sys
from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent
from amplifier_agent_engine._engine import assembly
from conformance.fixtures.replacement import create_engine
assembly.create_engine = create_engine

async def main():
    async with await create_agent(AgentOptions(provider='anthropic', model='claude-sonnet-5', storage=sys.argv[2])) as agent:
        if sys.argv[1] == 'create':
            session = await agent.create_session(SessionOptions(session_id='replacement-durable'))
        else:
            session = await agent.resume_session('replacement-durable')
            assert session.history[0].input.content[0].text == 'First greeting'
        turn = await session.start_turn(TurnInput([TextPart('First greeting' if sys.argv[1] == 'create' else 'Second greeting')]))
        events = [event async for event in turn.events()]
        assert events[-1].payload.state == 'success'
        print(json.dumps({'continuation': events[0].payload.continuation, 'turns': len(session.history)}))
        await session.close()

asyncio.run(main())
"""
    root = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AMPLIFIER_AGENT_")}
    environment["PYTHONPATH"] = str(root)
    observed = []
    for action in ("create", "resume"):
        completed = subprocess.run(
            [sys.executable, "-c", program, action, str(tmp_path / "store")],
            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        observed.append(json.loads(completed.stdout))
    assert observed == [{"continuation": "fresh", "turns": 1}, {"continuation": "resumed", "turns": 2}]


async def test_replacement_refuses_an_id_from_the_production_state_family(monkeypatch, tmp_path):
    from amplifier_agent import AgentError, AgentOptions, SessionOptions, create_agent
    from amplifier_agent_engine._engine import assembly

    from conformance.fixtures.scripted_provider import ScriptedFactory

    monkeypatch.setattr(assembly, "_provider_factory", ScriptedFactory())
    options = AgentOptions(provider="anthropic", model="claude-sonnet-5", storage=tmp_path)
    async with await create_agent(options) as agent:
        session = await agent.create_session(SessionOptions(session_id="original-family-session"))
        await session.close()
    monkeypatch.setattr(assembly, "create_engine", replacement.create_engine)
    async with await create_agent(options) as agent:
        with pytest.raises(AgentError) as caught:
            await agent.resume_session("original-family-session")
        assert caught.value.code == "not_found"
        assert caught.value.remedy
        assert await agent.list_sessions() == []


def test_replacement_imports_only_owned_records_and_callback_context():
    source = ast.parse(Path(replacement.__file__).read_text())
    dependencies = {
        node.module for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("amplifier_agent")
    }
    assert dependencies == {"amplifier_agent_engine._records", "amplifier_agent_engine._ports"}
    assert not any(
        isinstance(node, ast.Import) and any(alias.name.startswith("amplifier_agent") for alias in node.names)
        for node in ast.walk(source)
    )


def test_replacement_tool_helpers_use_only_owned_errors():
    source = ast.parse(Path(replacement.replacement_tools.__file__).read_text())
    dependencies = {
        node.module for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("amplifier_agent")
    }
    assert dependencies == {"amplifier_agent_engine._records"}
    assert not any(
        isinstance(node, ast.Import) and any(alias.name.startswith("amplifier_agent") for alias in node.names)
        for node in ast.walk(source)
    )


def test_replacement_node_participant_has_no_binding_or_production_policy_dependency():
    source = Path(replacement.__file__).with_name("replacement_host.mjs").read_text()
    imports = re.findall(r"(?:from\s*|import\s*\()['\"]([^'\"]+)['\"]", source)
    assert set(imports) == {"node:child_process", "node:url"}
    assert not re.search(r"\brequire\s*\(|\beval\s*\(|\bFunction\s*\(", source)
    assert "export async function createAgent" in source
