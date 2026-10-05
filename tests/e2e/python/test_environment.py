"""Per-agent environment, observed through bash, stdio MCP servers, and the provider wire."""

import json
import os
from pathlib import Path
import sys

from amplifier_agent import AgentError, AgentOptions, McpServer, SessionOptions, TextPart, TurnInput, create_agent
import pytest
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from tests.support.engine import provision
from tests.support.http_server import socket_server
from tests.support.provider_services import _openai

MCP_SERVICE = Path(__file__).parents[3] / "tests/support/mcp_service.py"


@pytest.fixture(autouse=True)
def separate_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("AGENT_MARKER", "SHARED", "LATE", "MCP_LEDGER", "MCP_CAPTURED"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def host(monkeypatch, tmp_path):
    directory = tmp_path / "host"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory.resolve()


def call(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


def options(**values):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow", **values)


async def run(agent, text="Perform the requested work."):
    async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
        turn = await session.start_turn(TurnInput([TextPart(text)]))
        return [event async for event in turn.events()]


async def test_environment_entries_reach_bash_over_the_process_environment(monkeypatch, host):
    monkeypatch.setenv("SHARED", "process")
    provision(monkeypatch, [call("bash", command='printf %s "$AGENT_MARKER:$SHARED" > seen'), {"text": "Done"}])
    async with await create_agent(options(environment={"AGENT_MARKER": "agent", "SHARED": "agent"})) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert (host / "seen").read_text() == "agent:agent"


async def test_environment_never_changes_the_process_environment(monkeypatch, host):
    monkeypatch.setenv("SHARED", "process")
    before = dict(os.environ)
    provision(monkeypatch, [call("bash", command="true"), {"text": "Done"}])
    async with await create_agent(options(environment={"AGENT_MARKER": "agent", "SHARED": "agent"})) as agent:
        assert dict(os.environ) == before
        await run(agent)
        assert dict(os.environ) == before
    assert dict(os.environ) == before


async def test_later_process_environment_changes_never_reach_an_existing_agent(monkeypatch, host):
    provision(monkeypatch, [call("bash", command='printf %s "${LATE-unset}" > seen'), {"text": "Done"}])
    async with await create_agent(options()) as agent:
        monkeypatch.setenv("LATE", "changed")
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert (host / "seen").read_text() == "unset"


async def test_two_agents_in_one_process_keep_their_own_environment(monkeypatch, host):
    provision(monkeypatch, [call("bash", command='printf x > "seen-$AGENT_MARKER"'), {"text": "Done"}])
    async with (
        await create_agent(options(environment={"AGENT_MARKER": "first"})) as first,
        await create_agent(options(environment={"AGENT_MARKER": "second"})) as second,
    ):
        await run(second)
        await run(first)
    assert sorted(path.name for path in host.iterdir()) == ["seen-first", "seen-second"]
    assert "AGENT_MARKER" not in os.environ


@pytest.mark.parametrize(
    ("environment", "named"),
    [({"": "value"}, None), ({"A=B": "value"}, "A=B"), ({"NUMBER": 1}, "NUMBER"), ({"NOTHING": None}, "NOTHING")],
    ids=["empty_name", "equals_in_name", "integer_value", "none_value"],
)
async def test_invalid_environment_entries_are_refused(monkeypatch, host, environment, named):
    probe = provision(monkeypatch, [{"text": "Unused"}])
    with pytest.raises(AgentError) as caught:
        await create_agent(options(environment=environment))
    assert caught.value.code == "invalid_input"
    assert caught.value.remedy
    assert "environment" in caught.value.message
    if named is not None:
        assert named in caught.value.message
    assert probe.requests == []


def keyed_service(seen):
    """An OpenAI Responses endpoint that records each request's credential."""

    async def complete(request):
        body = await request.json()
        seen.append(request.headers.get("authorization"))
        frames = _openai(body.get("model", "gpt-5"), None, False, True, {"value": "fixture"})

        async def events():
            for frame in frames:
                yield "event: " + frame["type"] + "\ndata: " + json.dumps(frame) + "\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return Starlette(routes=[Route(path, complete, methods=["POST"]) for path in ("/responses", "/v1/responses")])


async def test_provider_credentials_given_only_through_environment_reach_each_agents_requests(monkeypatch, host):
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    seen = []
    async with socket_server(keyed_service(seen)) as url:

        def keyed(key):
            return AgentOptions(
                provider="openai", model="gpt-5", environment={"OPENAI_API_KEY": key, "OPENAI_BASE_URL": url}
            )

        async with await create_agent(keyed("first-key")) as first, await create_agent(keyed("second-key")) as second:
            for agent in (first, second, first):
                assert (await run(agent, "Wire"))[-1].payload.state == "success"
    assert seen == ["Bearer first-key", "Bearer second-key", "Bearer first-key"]
    assert "OPENAI_API_KEY" not in os.environ


def ledger_server(**env):
    return McpServer("ledger", "stdio", command=sys.executable, args=[str(MCP_SERVICE)], env=env)


def ledger_script(monkeypatch):
    provision(monkeypatch, [call("mcp_ledger_record", value="once"), {"text": "Done"}])


def ledger(directory):
    return [json.loads(line) for line in (directory / "ledger.jsonl").read_text().splitlines()]


async def test_stdio_servers_start_in_the_process_directory_by_default(monkeypatch, host):
    ledger_script(monkeypatch)
    async with await create_agent(options(mcp_servers=[ledger_server(MCP_LEDGER="ledger.jsonl")])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert [entry["value"] for entry in ledger(host)] == ["once"]


async def test_stdio_servers_start_in_the_working_directory(monkeypatch, host, tmp_path):
    project = (tmp_path / "project").resolve()
    project.mkdir()
    ledger_script(monkeypatch)
    server = ledger_server(MCP_LEDGER="ledger.jsonl")
    async with await create_agent(options(working_directory=project, mcp_servers=[server])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert [entry["value"] for entry in ledger(project)] == ["once"]
    assert not (host / "ledger.jsonl").exists()


@pytest.mark.parametrize("server_value", [None, "server"])
async def test_stdio_server_env_applies_after_the_agent_environment(monkeypatch, host, server_value):
    monkeypatch.setenv("MCP_CAPTURED", "process")
    ledger_script(monkeypatch)
    env = {"MCP_LEDGER": "ledger.jsonl"}
    if server_value is not None:
        env["MCP_CAPTURED"] = server_value
    environment = {"MCP_CAPTURED": "agent", "MCP_LEDGER": "agent-ledger.jsonl"}
    async with await create_agent(options(environment=environment, mcp_servers=[ledger_server(**env)])) as agent:
        events = await run(agent)
    assert events[-1].payload.state == "success"
    assert [entry["captured"] for entry in ledger(host)] == [server_value or "agent"]
    assert not (host / "agent-ledger.jsonl").exists()
