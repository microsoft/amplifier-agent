"""Image input through Python handles, observed at the scripted provider."""

import asyncio
import json
from pathlib import Path
import sys

from amplifier_agent import (
    AgentError,
    AgentOptions,
    ConversationMessage,
    ImagePart,
    SessionOptions,
    TextPart,
    TurnInput,
    create_agent,
)
import pytest

from tests.support.engine import provision as provision_engine

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
GIF = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
NO_VISION = ["tools", "streaming"]


@pytest.fixture
def provider(monkeypatch):
    def provision(script=None):
        return provision_engine(monkeypatch, script or [{"chunks": ["Seen"], "text": "Seen"}])

    return provision


def image(media_type, data):
    part = ImagePart(media_type=media_type, data=data)
    assert part.type == "image"
    return part


def block(part):
    if part.type == "text":
        return {"type": "text", "text": part.text}
    return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": part.data}}


def observed(message):
    return [
        {"type": "text", "text": item["text"]}
        if item["type"] == "text"
        else {"type": item["type"], "source": item.get("source")}
        for item in message["content"]
    ]


def options(**values):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", **values)


def named(error, code):
    assert isinstance(error, AgentError)
    assert error.code == code
    assert error.message.strip()
    assert error.remedy.strip()
    assert type(error.retryable) is bool


@pytest.mark.parametrize("capabilities", [None, ["tools", "vision"]], ids=["unreported", "vision"])
async def test_content_images_reach_the_provider_in_order(provider, capabilities):
    probe = provider()
    probe.model_capabilities = capabilities
    content = [TextPart("Compare these"), image("image/png", PNG), TextPart("with"), image("image/gif", GIF)]
    input = TurnInput(content)
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(input)
        events = [event async for event in turn.events()]
        result = events[-1].payload
        assert result.state == "success"
        assert all(part.type == "text" for part in result.content or [])
        assert session.history[0].input == input
    assert len(probe.requests) == 1
    last = probe.requests[0]["messages"][-1]
    assert last["role"] == "user"
    assert observed(last) == [block(part) for part in content]


async def test_history_user_image_reaches_the_provider(provider):
    probe = provider()
    history = [
        ConversationMessage("user", [TextPart("Earlier picture"), image("image/jpeg", PNG)]),
        ConversationMessage("assistant", [TextPart("Earlier answer")]),
    ]
    input = TurnInput([TextPart("What was in it?")], history=history)
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        assert (await session.run(input)).state == "success"
        assert session.history[0].input == input
    messages = probe.requests[0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert observed(messages[0]) == [block(part) for part in history[0].content]


@pytest.mark.parametrize(
    ("fault", "field"),
    [
        ("assistant", "input.history[0].content[1]"),
        ("system", "input.history[0].content[1]"),
        ("developer", "input.history[0].content[1]"),
        ("base64", "input.content[1]"),
        ("media_type", "input.history[0].content[0]"),
    ],
)
async def test_invalid_image_refusal_keeps_first_turn_available(provider, fault, field):
    probe = provider()
    if fault in {"assistant", "system", "developer"}:
        input = TurnInput([], history=[ConversationMessage(fault, [TextPart("Earlier"), image("image/png", PNG)])])
    elif fault == "base64":
        input = TurnInput([TextPart("Look"), image("image/png", "not base64!!")])
    else:
        input = TurnInput([TextPart("Look")], history=[ConversationMessage("user", [image("image/bmp", PNG)])])
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        for operation in [session.run, session.start_turn]:
            with pytest.raises(AgentError) as caught:
                await operation(input)
            named(caught.value, "invalid_input")
            assert caught.value.details is not None
            assert caught.value.details["field"].startswith(field)
            assert session.history == []
            assert probe.requests == []
        turn = await session.start_turn(
            TurnInput([TextPart("Look")], history=[ConversationMessage("user", [image("image/png", PNG)])])
        )
        events = [event async for event in turn.events()]
        assert events[0].payload.continuation == "fresh"
        assert events[-1].payload.state == "success"
        assert len(probe.requests) == 1


async def test_reported_non_vision_model_refuses_images_before_provider_work(provider):
    probe = provider()
    probe.model_capabilities = NO_VISION
    input = TurnInput([TextPart("Describe"), image("image/png", PNG)])
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        for operation in [session.run, session.start_turn]:
            with pytest.raises(AgentError) as caught:
                await operation(input)
            named(caught.value, "image_unsupported")
            assert session.history == []
            assert probe.requests == []
        assert (await session.run(TurnInput([TextPart("Text only")]))).state == "success"
        assert len(probe.requests) == 1


async def test_text_turn_on_reported_non_vision_model_succeeds(provider):
    probe = provider()
    probe.model_capabilities = NO_VISION
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        assert (await session.run(TurnInput([TextPart("Text only")]))).state == "success"
    assert len(probe.requests) == 1


async def test_provider_rejection_of_an_image_fails_image_unsupported_in_terminal(provider):
    probe = provider([{"reject_images": True, "text": "Unreachable"}])
    input = TurnInput([TextPart("Describe"), image("image/webp", PNG)])
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(input)
        events = [event async for event in turn.events()]
        result = events[-1].payload
        assert events[-1].type == "terminal"
        assert result.state == "failure"
        assert result.error is not None
        named(result.error, "image_unsupported")
    assert len(probe.requests) == 1


def holds_image_block(request):
    return any(
        isinstance(message["content"], list) and any(item["type"] == "image" for item in message["content"])
        for message in request["messages"]
    )


async def test_a_generic_refusal_of_an_image_request_is_provider_failed(provider):
    probe = provider([{"reject_images": "max_tokens is too large: 900000", "text": "Unreachable"}])
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(TurnInput([TextPart("Describe"), image("image/png", PNG)]))
    assert result.state == "failure"
    named(result.error, "provider_failed")
    assert result.error is not None
    assert result.error.details is not None
    assert "max_tokens is too large" in result.error.details["provider_message"]
    assert len(probe.requests) == 1


async def test_an_image_refused_in_terminal_leaves_its_description_for_the_next_turn(provider):
    probe = provider([{"reject_images": True, "text": "Unreachable"}, {"text": "Answered"}])
    history = [ConversationMessage("user", [TextPart("Earlier"), image("image/gif", GIF)])]
    input = TurnInput([TextPart("Describe"), image("image/png", PNG)], history=history)
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        refused = await session.run(input)
        assert refused.state == "failure"
        named(refused.error, "image_unsupported")
        assert session.history[0].input == input
        assert (await session.run(TurnInput([TextPart("Go on")]))).state == "success"
    assert len(probe.requests) == 2
    request = probe.requests[1]
    assert not holds_image_block(request)
    users = [observed(message) for message in request["messages"] if message["role"] == "user"]
    assert users[:2] == [
        [{"type": "text", "text": "Earlier"}, {"type": "text", "text": "[image: image/gif, 42 bytes]"}],
        [{"type": "text", "text": "Describe"}, {"type": "text", "text": "[image: image/png, 70 bytes]"}],
    ]


async def test_a_durable_image_refused_in_terminal_resumes_described(provider, tmp_path):
    provider([{"reject_images": True, "text": "Unreachable"}])
    input = TurnInput([TextPart("Describe"), image("image/png", PNG)])
    async with (
        await create_agent(options(sessions_directory=tmp_path)) as agent,
        await agent.create_session(SessionOptions(session_id="refused-image")) as session,
    ):
        refused = await session.run(input)
        named(refused.error, "image_unsupported")
    directory = tmp_path / "sessions" / "refused-image"
    assert PNG not in (directory / "transcript.jsonl").read_text()
    assert PNG in (directory / "turns.jsonl").read_text()
    probe = provider([{"text": "Answered"}])
    async with (
        await create_agent(options(sessions_directory=tmp_path)) as agent,
        await agent.resume_session("refused-image") as resumed,
    ):
        assert resumed.history[0].input == input
        assert (await resumed.run(TurnInput([TextPart("Go on")]))).state == "success"
    [request] = probe.requests
    assert not holds_image_block(request)
    assert observed(request["messages"][0]) == [
        {"type": "text", "text": "Describe"},
        {"type": "text", "text": "[image: image/png, 70 bytes]"},
    ]


async def test_images_of_earlier_turns_stay_when_a_later_turn_is_refused_for_them(provider, tmp_path):
    probe = provider([{"text": "Saw it"}])
    async with (
        await create_agent(options(sessions_directory=tmp_path)) as agent,
        await agent.create_session(SessionOptions(session_id="kept-image")) as session,
    ):
        assert (await session.run(TurnInput([TextPart("Remember"), image("image/png", PNG)]))).state == "success"
    probe.model_capabilities = NO_VISION
    transcript = tmp_path / "sessions" / "kept-image" / "transcript.jsonl"
    async with (
        await create_agent(options(sessions_directory=tmp_path)) as agent,
        await agent.resume_session("kept-image") as resumed,
    ):
        with pytest.raises(AgentError) as caught:
            await resumed.run(TurnInput([TextPart("Text only")]))
        named(caught.value, "image_unsupported")
        assert "earlier turns" in caught.value.remedy
        assert len(resumed.history) == 1
    assert PNG in transcript.read_text()
    assert len(probe.requests) == 1


DURABLE = """
import asyncio, json, sys
from amplifier_agent import AgentOptions, ImagePart, SessionOptions, TextPart, TurnInput, create_agent
from tests.support.scripted_provider import install
async def main():
    install([{'text': 'Saw it', 'chunks': ['Saw it']}])
    options = AgentOptions(provider='anthropic', model='claude-sonnet-5', sessions_directory=sys.argv[1])
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(session_id='image-session')) as session:
            input = TurnInput([TextPart('Remember this'), ImagePart(media_type='image/png', data=sys.argv[2])])
            result = await session.run(input)
    print(json.dumps({'state': result.state}), flush=True)
asyncio.run(main())
"""


async def test_durable_image_turn_resumes_in_another_process_and_forks(provider, tmp_path):
    probe = provider([{"text": "Resumed"}, {"text": "Forked"}])
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        DURABLE,
        str(tmp_path),
        PNG,
        cwd=Path(__file__).parents[3],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
    assert child.returncode == 0, stderr.decode()
    assert json.loads(stdout.splitlines()[-1]) == {"state": "success"}
    directory = tmp_path / "sessions" / "image-session"
    assert PNG in (directory / "turns.jsonl").read_text()
    assert PNG in (directory / "transcript.jsonl").read_text()
    expected = TurnInput([TextPart("Remember this"), image("image/png", PNG)])
    first_user = [block(part) for part in expected.content]
    async with (
        await create_agent(options(sessions_directory=tmp_path)) as agent,
        await agent.resume_session("image-session") as resumed,
    ):
        assert resumed.history[0].input == expected
        assert (await resumed.run(TurnInput([TextPart("What did you see?")]))).state == "success"
        async with await resumed.fork() as forked:
            assert forked.history == resumed.history
            assert (await forked.run(TurnInput([TextPart("And now?")]))).state == "success"
    assert len(probe.requests) == 2
    for request in probe.requests:
        users = [message for message in request["messages"] if message["role"] == "user"]
        assert observed(users[0]) == first_user


async def test_copilot_refuses_history_images_at_the_method(provider):
    probe = provider()
    input = TurnInput([TextPart("And this?")], history=[ConversationMessage("user", [image("image/png", PNG)])])
    async with (
        await create_agent(AgentOptions(provider="github-copilot", model="claude-sonnet-4")) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        for operation in [session.run, session.start_turn]:
            with pytest.raises(AgentError) as caught:
                await operation(input)
            named(caught.value, "image_unsupported")
            assert session.history == []
        assert (await session.run(TurnInput([TextPart("Look"), image("image/png", PNG)]))).state == "success"
    assert len(probe.requests) == 1
