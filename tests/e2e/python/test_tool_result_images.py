"""Tool results holding images, observed at the scripted provider and in events."""

import base64
import json
from pathlib import Path
import sys
from typing import Any

from amplifier_agent import AgentOptions, ImagePart, McpServer, SessionOptions, TextPart, Tool, TurnInput, create_agent
import pytest

from tests.support.engine import provision

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PNG_LINE = f"[image: image/png, {len(base64.b64decode(PNG))} bytes]"
SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}
SCRIPT = [{"tool": {"name": "look", "arguments": {}}}, {"chunks": ["Seen"], "text": "Seen"}]
MCP_SERVICE = Path(__file__).parents[3] / "tests/support/mcp_service.py"


def looking(result):
    async def handler(arguments, context):
        return result

    return Tool("look", "Look at a picture.", SCHEMA, handler)


def image_block(data=PNG, media_type="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def tool_messages(request):
    return [message for message in request["messages"] if message["role"] == "tool"]


def blocks(message):
    content = message["content"]
    if isinstance(content, str):
        return content
    return [
        {"type": "text", "text": item["text"]} if item["type"] == "text" else image_block(**_source(item))
        for item in content
    ]


def _source(item):
    return {"data": item["source"]["data"], "media_type": item["source"]["media_type"]}


async def run(result, script=SCRIPT, **options):
    values: dict[str, Any] = {"provider": "anthropic", "model": "claude-sonnet-5", **options}
    if result is not None:
        values.setdefault("tools", [looking(result)])
    values.setdefault("approvals", "allow")
    async with (
        await create_agent(AgentOptions(**values)) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("Look at it")]))
        return [event async for event in turn.events()]


def resolution(events):
    return next(event.payload.resolution for event in events if event.type == "tool_result")


def no_bytes(events):
    return all(PNG not in repr(event.payload) for event in events)


async def test_text_and_image_parts_reach_the_model_while_events_describe_the_image(monkeypatch):
    probe = provision(monkeypatch, SCRIPT)
    events = await run([TextPart("Caption"), ImagePart(media_type="image/png", data=PNG)])
    result = events[-1].payload
    assert result.state == "success", result.error
    resolved = resolution(events)
    assert resolved.outcome == "completed"
    assert resolved.content == f"Caption\n{PNG_LINE}"
    assert resolved.truncated is False
    assert no_bytes(events)
    assert len(probe.requests) == 2
    [message] = tool_messages(probe.requests[1])
    assert blocks(message) == [{"type": "text", "text": "Caption"}, image_block()]


async def test_an_empty_list_result_is_empty_text(monkeypatch):
    probe = provision(monkeypatch, SCRIPT)
    events = await run([])
    assert events[-1].payload.state == "success"
    assert resolution(events).content == ""
    assert tool_messages(probe.requests[1])[0]["content"] == ""


@pytest.mark.parametrize(
    ("result", "named"),
    [
        ([ImagePart(media_type="image/bmp", data=PNG)], "image/bmp"),
        ([ImagePart(media_type="image/png", data="not base64!!")], "base64"),
        ([ImagePart(media_type="image/png", data="")], "data"),
        ([TextPart("Caption"), "loose text"], "result[1]"),
        ([{"type": "image", "media_type": "image/png", "data": PNG}], "result[0]"),
    ],
    ids=["media-type", "base64", "empty", "string", "mapping"],
)
async def test_malformed_parts_fail_tool_result_invalid(monkeypatch, result, named):
    probe = provision(monkeypatch, SCRIPT)
    events = await run(result)
    terminal = events[-1].payload
    assert terminal.state == "failure"
    assert terminal.error.code == "tool_result_invalid"
    assert named in terminal.error.message
    assert terminal.error.remedy
    resolved = resolution(events)
    assert resolved.outcome == "unknown"
    assert resolved.error.code == "tool_result_invalid"
    assert len(probe.requests) == 1


async def test_the_ceiling_caps_text_and_carries_the_image_whole(monkeypatch):
    probe = provision(monkeypatch, SCRIPT)
    parts = [TextPart("x" * 30), ImagePart(media_type="image/png", data=PNG), TextPart("y" * 5)]
    events = await run(parts, tool_result_max_bytes=10)
    assert events[-1].payload.state == "success"
    marker = "...[tool output reached limit: kept 10 of 35 bytes]"
    resolved = resolution(events)
    assert resolved.truncated is True
    assert resolved.original_bytes == 35
    assert resolved.content == f"{'x' * 10}\n{marker}\n{PNG_LINE}"
    [message] = tool_messages(probe.requests[1])
    assert blocks(message) == [{"type": "text", "text": f"{'x' * 10}\n{marker}"}, image_block()]


@pytest.mark.parametrize(
    ("provider", "model"),
    [("gemini", "gemini-2.5-flash"), ("openai", "o3-mini"), ("chat-completions", "local-model")],
)
async def test_a_provider_without_tool_result_images_fails_before_sending_them(monkeypatch, provider, model):
    probe = provision(monkeypatch, SCRIPT)
    if provider == "chat-completions":
        monkeypatch.setenv("CHAT_COMPLETIONS_BASE_URL", "http://127.0.0.1:9/v1")
    events = await run(
        [TextPart("Caption"), ImagePart(media_type="image/png", data=PNG)], provider=provider, model=model
    )
    terminal = events[-1]
    assert terminal.type == "terminal"
    assert terminal.payload.state == "failure"
    assert terminal.payload.error.code == "image_unsupported"
    assert "tool results" in terminal.payload.error.message
    assert resolution(events).outcome == "completed"
    assert len(probe.requests) == 1
    assert PNG not in json.dumps(probe.requests)


def holds_image_block(request):
    return any(
        isinstance(message["content"], list) and any(item["type"] == "image" for item in message["content"])
        for message in request["messages"]
    )


def refused_options(**values):
    return AgentOptions(
        provider="gemini",
        model="gemini-2.5-flash",
        approvals="allow",
        tools=[looking([TextPart("Caption"), ImagePart(media_type="image/png", data=PNG)])],
        **values,
    )


async def test_a_refused_tool_result_image_leaves_its_description_for_the_next_turn(monkeypatch):
    probe = provision(monkeypatch, SCRIPT)
    async with (
        await create_agent(refused_options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        refused = await session.run(TurnInput([TextPart("Look at it")]))
        assert refused.state == "failure"
        assert refused.error is not None
        assert refused.error.code == "image_unsupported"
        assert "description" in refused.error.remedy
        assert (await session.run(TurnInput([TextPart("Go on")]))).state == "success"
    assert len(probe.requests) == 2
    assert not holds_image_block(probe.requests[1])
    assert PNG not in json.dumps(probe.requests[1])
    [message] = tool_messages(probe.requests[1])
    assert message["content"] == f"Caption\n{PNG_LINE}"


async def test_a_refused_durable_tool_result_image_is_described_on_resume_and_fork(monkeypatch, tmp_path):
    provision(monkeypatch, SCRIPT)
    options = refused_options(sessions_directory=tmp_path)
    async with (
        await create_agent(options) as agent,
        await agent.create_session(SessionOptions(session_id="refused-image")) as session,
    ):
        refused = await session.run(TurnInput([TextPart("Look at it")]))
        assert refused.error is not None
        assert refused.error.code == "image_unsupported"
    directory = tmp_path / "sessions" / "refused-image"
    transcript = (directory / "transcript.jsonl").read_text()
    assert PNG not in transcript
    assert json.dumps(PNG_LINE)[1:-1] in transcript
    probe = provision(monkeypatch, [{"text": "Answered"}])
    async with (
        await create_agent(options) as agent,
        await agent.resume_session("refused-image") as resumed,
    ):
        assert (await resumed.run(TurnInput([TextPart("What was it?")]))).state == "success"
        async with await resumed.fork() as forked:
            assert (await forked.run(TurnInput([TextPart("And now?")]))).state == "success"
    assert len(probe.requests) == 2
    for request in probe.requests:
        assert not holds_image_block(request)
        [message] = tool_messages(request)
        assert message["content"] == f"Caption\n{PNG_LINE}"


async def test_a_durable_tool_result_image_survives_resume_and_fork(monkeypatch, tmp_path):
    first = provision(monkeypatch, SCRIPT)
    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        approvals="allow",
        sessions_directory=tmp_path,
        tools=[looking([TextPart("Caption"), ImagePart(media_type="image/png", data=PNG)])],
    )
    async with (
        await create_agent(options) as agent,
        await agent.create_session(SessionOptions(session_id="tool-image")) as session,
    ):
        assert (await session.run(TurnInput([TextPart("Look at it")]))).state == "success"
    transcript = tmp_path / "sessions" / "tool-image" / "transcript.jsonl"
    assert PNG in transcript.read_text()
    assert len(first.requests) == 2
    probe = provision(monkeypatch, [{"text": "Answered"}])
    async with (
        await create_agent(options) as agent,
        await agent.resume_session("tool-image") as resumed,
    ):
        assert (await resumed.run(TurnInput([TextPart("What was it?")]))).state == "success"
        async with await resumed.fork() as forked:
            assert (await forked.run(TurnInput([TextPart("And now?")]))).state == "success"
    assert len(probe.requests) == 2
    for request in probe.requests:
        [message] = tool_messages(request)
        assert blocks(message) == [{"type": "text", "text": "Caption"}, image_block()]


@pytest.mark.parametrize("media_type", ["image/png", "image/bmp"])
async def test_mcp_image_content_is_an_image_part(monkeypatch, tmp_path, media_type):
    script = [{"tool": {"name": "mcp_ledger_picture", "arguments": {"media_type": media_type}}}, {"text": "Seen"}]
    probe = provision(monkeypatch, script)
    server = McpServer(
        "ledger",
        "stdio",
        command=sys.executable,
        args=[str(MCP_SERVICE)],
        env={"MCP_LEDGER": str(tmp_path / "ledger.jsonl")},
    )
    events = await run(None, script, mcp_servers=[server])
    assert no_bytes(events)
    if media_type == "image/bmp":
        terminal = events[-1].payload
        assert terminal.state == "failure"
        assert terminal.error.code == "tool_result_invalid"
        assert "'image/bmp'" in terminal.error.message
        assert len(probe.requests) == 1
        return
    assert events[-1].payload.state == "success"
    assert resolution(events).content == f"A picture\n{PNG_LINE}"
    [message] = tool_messages(probe.requests[1])
    assert blocks(message) == [{"type": "text", "text": "A picture"}, image_block()]
