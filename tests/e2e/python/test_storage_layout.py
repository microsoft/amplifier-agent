import asyncio
import json
import shutil
from pathlib import Path

import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.engine import provision as provision_engine
from conformance.fixtures.engine import provision_many

PROMPT = TurnInput([TextPart("Another greeting")])
FIXTURE_SECRET = "tp_fixture_secret_00001"


@pytest.fixture
def provider(monkeypatch):
    return provision_engine(monkeypatch)


def options(storage, **values):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", storage=storage, **values)


def session_dir(root, session_id, workspace="default"):
    return Path(root) / "workspaces" / workspace / "sessions" / session_id


def events(root, session_id):
    path = session_dir(root, session_id) / "context-intelligence" / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def named(error, code):
    assert error.value.code == code
    assert error.value.remedy


class Destination:
    """Loopback Context Intelligence server recording every POST /events body."""

    def __init__(self):
        self.received = []
        self.server = None
        self.connections = set()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        for writer in list(self.connections):
            writer.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)

    async def _serve(self, reader, writer):
        self.connections.add(writer)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                request, *headers = head.decode().split("\r\n")
                length = next(
                    (int(h.split(":", 1)[1]) for h in headers if h.lower().startswith("content-length:")),
                    0,
                )
                body = await reader.readexactly(length) if length else b""
                method, target, _ = request.split(" ", 2)
                self.received.append({
                    "method": method,
                    "target": target,
                    "headers": dict(h.split(": ", 1) for h in headers if ": " in h),
                    "body": json.loads(body) if body else None,
                })
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\n{}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.connections.discard(writer)
            writer.close()


@pytest.mark.production_only
async def test_durable_turn_writes_the_contracted_layout_and_a_redacted_capture(provider, tmp_path):
    reply = f"The key is {FIXTURE_SECRET}"
    provider.script = [{"text": reply, "chunks": [reply]}]
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(session_id="layout-session"))
        result = await session.run(TurnInput([TextPart(f"Use the key {FIXTURE_SECRET} now")]))
        assert result.state == "success"
        assert result.content == [TextPart(reply)]
        await session.close()
    directory = session_dir(tmp_path, "layout-session")
    assert (directory / "transcript.jsonl").is_file()
    assert (directory / "metadata.json").is_file()
    assert (directory / "context-intelligence" / "events.jsonl").is_file()
    assert json.loads((directory / "metadata.json").read_text())["session_id"] == "layout-session"
    transcripts = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("transcript.jsonl"))
    assert transcripts == [Path("workspaces/default/sessions/layout-session/transcript.jsonl")]
    assert not any(path.suffix in {".sqlite3", ".db"} for path in tmp_path.rglob("*"))
    captured = events(tmp_path, "layout-session")
    names = [event["event"] for event in captured]
    assert names[0] == "session:start"
    assert {"prompt:submit", "execution:start", "execution:end", "session:end"} <= set(names)
    assert all(event["data"]["session_id"] == "layout-session" for event in captured)
    text = (directory / "context-intelligence" / "events.jsonl").read_text()
    assert FIXTURE_SECRET not in text
    assert "[REDACTED:SECRET]" in text
    assert (directory / "transcript.jsonl").read_text().count(FIXTURE_SECRET) == 2
    assert (directory / "turns.jsonl").read_text().count(FIXTURE_SECRET) == 2


@pytest.mark.production_only
async def test_capture_redaction_never_changes_what_the_model_reads(provider, tmp_path):
    output = f"token={FIXTURE_SECRET}"

    async def reveal(arguments, context):
        return output

    tool = Tool(
        "reveal",
        "Return a credential.",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        reveal,
    )
    provider.script = [
        {"tool": {"name": "reveal", "arguments": {}}},
        {"text": "Finished", "chunks": ["Finished"]},
    ]
    async with await create_agent(options(tmp_path, tools=[tool], approvals="allow")) as agent:
        session = await agent.create_session(SessionOptions(session_id="reveal-session"))
        result = await session.run(PROMPT)
        assert result.state == "success"
        await session.close()
    tool_messages = [m for m in provider.requests[-1]["messages"] if m["role"] == "tool"]
    assert [m["content"] for m in tool_messages] == [output]
    captured = events(tmp_path, "reveal-session")
    post = [event for event in captured if event["event"] == "tool:post"]
    assert len(post) == 1
    assert FIXTURE_SECRET not in json.dumps(post[0])
    assert "[REDACTED:SECRET]" in json.dumps(post[0]["data"]["result"])
    assert (session_dir(tmp_path, "reveal-session") / "transcript.jsonl").read_text().count(output) == 1


@pytest.mark.production_only
async def test_capture_is_observation_not_authority(provider, tmp_path):
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(session_id="observed-session"))
        await session.run(PROMPT)
        history = session.history
        await session.close()
    shutil.rmtree(session_dir(tmp_path, "observed-session") / "context-intelligence")
    async with await create_agent(options(tmp_path)) as agent:
        assert [record.session_id for record in await agent.list_sessions()] == ["observed-session"]
        resumed = await agent.resume_session("observed-session")
        assert resumed.history == history
        assert (await resumed.run(PROMPT)).state == "success"
        assert len(resumed.history) == 2
        await resumed.close()
    captured = events(tmp_path, "observed-session")
    assert captured[0]["event"] == "session:resume"


@pytest.mark.production_only
async def test_resume_appends_to_the_same_capture(provider, tmp_path):
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(session_id="appended-session"))
        await session.run(PROMPT)
        await session.close()
    before = len(events(tmp_path, "appended-session"))
    async with await create_agent(options(tmp_path)) as agent:
        resumed = await agent.resume_session("appended-session")
        await resumed.run(PROMPT)
        await resumed.close()
    captured = events(tmp_path, "appended-session")
    assert len(captured) > before
    assert captured[before]["event"] == "session:resume"
    assert all(event["data"]["session_id"] == "appended-session" for event in captured)


@pytest.mark.production_only
async def test_transcript_ahead_of_the_commit_point_is_invisible_after_resume(provider, tmp_path):
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(session_id="torn-session"))
        await session.run(TurnInput([TextPart("Committed input")]))
        history = session.history
        await session.close()
    transcript = session_dir(tmp_path, "torn-session") / "transcript.jsonl"
    with transcript.open("a") as stream:
        stream.write(json.dumps({"role": "user", "content": "Uncommitted input"}) + "\n")
        stream.write(json.dumps({"role": "assistant", "content": "Uncommitted reply"}) + "\n")
    async with await create_agent(options(tmp_path)) as agent:
        resumed = await agent.resume_session("torn-session")
        assert resumed.history == history
        assert (await resumed.run(PROMPT)).state == "success"
        serialized = json.dumps(provider.requests[-1])
        assert "Committed input" in serialized
        assert "Uncommitted" not in serialized
        await resumed.close()


@pytest.mark.production_only
async def test_ephemeral_session_is_captured_but_never_stored(provider, tmp_path):
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        session_id = session.info.session_id
        await session.run(PROMPT)
        assert await agent.list_sessions() == []
        await session.close()
        directory = session_dir(tmp_path, session_id)
        assert (directory / "context-intelligence" / "events.jsonl").is_file()
        assert not (directory / "transcript.jsonl").exists()
        assert not (directory / "metadata.json").exists()
        assert await agent.list_sessions() == []
        with pytest.raises(AgentError) as error:
            await agent.resume_session(session_id)
        named(error, "not_found")


@pytest.mark.production_only
async def test_delegated_work_is_captured_outside_the_session_list(monkeypatch, tmp_path):
    provision_many(
        monkeypatch,
        [{"tool": {"name": "delegate", "arguments": {"instruction": "Summarize"}}},
         {"text": "Finished", "chunks": ["Finished"]}],
        [{"text": "Delegated reply", "chunks": ["Delegated reply"]}],
    )
    async with await create_agent(options(tmp_path, approvals="allow")) as agent:
        session = await agent.create_session(SessionOptions(session_id="parent-session"))
        assert (await session.run(PROMPT)).state == "success"
        await session.close()
        assert [record.session_id for record in await agent.list_sessions()] == ["parent-session"]
    sessions = Path(tmp_path) / "workspaces" / "default" / "sessions"
    children = [path for path in sessions.iterdir() if path.is_dir() and path.name != "parent-session"]
    assert len(children) == 1
    assert "_" in children[0].name
    assert not (children[0] / "transcript.jsonl").exists()
    child_events = [
        json.loads(line)
        for line in (children[0] / "context-intelligence" / "events.jsonl").read_text().splitlines()
    ]
    assert child_events and all(event["data"]["parent_id"] == "parent-session" for event in child_events)


@pytest.mark.production_only
async def test_named_destination_receives_the_capture_and_refusal_never_fails_a_turn(
    provider, tmp_path, monkeypatch
):
    foreign_home = tmp_path / "foreign-home"
    (foreign_home / ".amplifier").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(foreign_home))
    async with Destination() as accepted, Destination() as foreign:
        (foreign_home / ".amplifier" / "settings.yaml").write_text(
            "overrides:\n  hook-context-intelligence:\n    config:\n      destinations:\n"
            f"        foreign:\n          url: {foreign.url}\n          api_key: foreign-key\n"
            "          include: ['**']\n"
        )
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", foreign.url)
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", "foreign-key")
        host = tmp_path / "host.json"
        host.write_text(json.dumps({
            "context_intelligence": {
                "destinations": {
                    "accepted": {"url": accepted.url, "api_key": "fixture-key"},
                    "refused": {"url": "http://127.0.0.1:9", "api_key": "fixture-key"},
                    "excluded": {"url": foreign.url, "api_key": "fixture-key", "include": []},
                }
            }
        }))
        monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))
        async with await create_agent(options(tmp_path)) as agent:
            session = await agent.create_session(SessionOptions(session_id="forwarded-session"))
            result = await session.run(PROMPT)
            assert result.state == "success"
            await session.close()
        for _ in range(50):
            if any(item["body"]["event"] == "session:end" for item in accepted.received):
                break
            await asyncio.sleep(0.1)
        posted = [item for item in accepted.received if item["method"] == "POST"]
        assert posted and all(item["target"] == "/events" for item in posted)
        assert all(item["headers"].get("Authorization") == "Bearer fixture-key" for item in posted)
        assert all(item["body"]["data"]["session_id"] == "forwarded-session" for item in posted)
        assert {item["body"]["event"] for item in posted} >= {"session:start", "prompt:submit", "session:end"}
        assert all(item["body"]["workspace"] == "default" for item in posted)
        assert foreign.received == []
    assert events(tmp_path, "forwarded-session")


@pytest.mark.production_only
async def test_context_intelligence_is_settings_only(provider, tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_AGENT_CONTEXT_INTELLIGENCE", "{}")
    with pytest.raises(AgentError) as error:
        await create_agent(options(tmp_path))
    named(error, "invalid_input")
    assert error.value.details["field"] == "AMPLIFIER_AGENT_CONTEXT_INTELLIGENCE"
    monkeypatch.delenv("AMPLIFIER_AGENT_CONTEXT_INTELLIGENCE")
    host = tmp_path / "host.json"
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))
    for settings, field in (
        ({"context_intelligence": []}, "context_intelligence"),
        ({"context_intelligence": {"server_url": "http://x"}}, "context_intelligence.server_url"),
        ({"context_intelligence": {"destinations": {"a": {"api_key": "k"}}}},
         "context_intelligence.destinations.a.url"),
        ({"context_intelligence": {"destinations": {"a": {"url": "http://x", "token": "k"}}}},
         "context_intelligence.destinations.a.token"),
        ({"context_intelligence": {"destinations": {"a": {"url": "http://x"}}}},
         "context_intelligence.destinations.a"),
        ({"context_intelligence": {"destinations": {"a": {"url": "http://x", "auth_mode": "magic"}}}},
         "context_intelligence.destinations.a.auth_mode"),
    ):
        host.write_text(json.dumps(settings))
        with pytest.raises(AgentError) as error:
            await create_agent(options(tmp_path))
        named(error, "invalid_input")
        assert error.value.details["field"] == field
    host.write_text(json.dumps({"context_intelligence": {"destinations": {}}}))
    async with await create_agent(options(tmp_path)) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        assert (await session.run(PROMPT)).state == "success"
