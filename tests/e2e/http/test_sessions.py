"""Model selection and server-held sessions on the HTTP face (http-face.v1 sections 5 and 12)."""

import asyncio
from contextlib import asynccontextmanager
import json

from amplifier_agent import AgentOptions, Session
from amplifier_agent_http import Settings, create_app
import httpx
import pytest

from tests.support import http_shapes
from tests.support.engine import provision
from tests.support.http_server import socket_server

TOKEN = "[REDACTED:SECRET]"
SESSION = "orchard-planning-01"


@asynccontextmanager
async def face(monkeypatch, tmp_path, script):
    probe = provision(monkeypatch, script)
    options = AgentOptions(
        provider="anthropic", model="claude-sonnet-5", tools=[], sessions_directory=tmp_path / "sessions"
    )
    app = create_app(Settings(token=TOKEN), options)
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}) as client,
    ):
        yield app, client, probe


def replies(*texts):
    """Each provider connection, one per session handle, answers from the start of this script."""
    return [{"chunks": [text], "text": text} for text in texts]


def conversation(*texts):
    return [{"role": "user" if index % 2 == 0 else "assistant", "content": text} for index, text in enumerate(texts)]


def completion(messages, **fields):
    return {"model": "amplifier", "messages": messages, **fields}


def sent_text(request):
    """The user and assistant text a provider request carried, in order."""
    texts = []
    for message in request["messages"]:
        if message["role"] not in ("user", "assistant"):
            continue
        content = message["content"]
        if isinstance(content, str):
            texts.append(content)
        else:
            texts.extend(block["text"] for block in content if block.get("type") == "text")
    return texts


async def stored_turns(app, session_id):
    session = await app.state.agent.resume_session(session_id)
    try:
        return [[part.text for part in record.input.content] for record in session.history], session.info
    finally:
        await session.close()


async def test_a_session_continues_from_its_own_turns(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, replies("Noted.", "Orchard.")) as (app, client, probe):
        headers = {"Amplifier-Session-Id": SESSION}
        first = await client.post("/v1/chat/completions", headers=headers, json=completion(conversation("Orchard.")))
        assert first.status_code == 200, first.text
        # The client sends back a different reply; the session holds its own.
        messages = conversation("Orchard.", "Client-side copy.", "Name?")
        second = await client.post("/v1/chat/completions", headers=headers, json=completion(messages))
        assert second.status_code == 200, second.text
        assert sent_text(probe.requests[1]) == ["Orchard.", "Noted.", "Name?"]
        turns, info = await stored_turns(app, SESSION)
    assert turns == [["Orchard."], ["Name?"]]
    assert info.persistence == "durable"


async def test_a_session_accepts_the_reasoning_it_returned_and_ignores_it(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, replies("Noted.", "Orchard.")) as (app, client, probe):
        headers = {"Amplifier-Session-Id": SESSION}
        first = await client.post("/v1/chat/completions", headers=headers, json=completion(conversation("Orchard.")))
        assert first.status_code == 200, first.text
        messages = conversation("Orchard.", "Noted.", "Name?")
        messages[1]["reasoning_content"] = "Echoed thinking"
        refused = [*messages[:2], {**messages[2], "reasoning_content": "Echoed thinking"}]
        response = await client.post("/v1/chat/completions", headers=headers, json=completion(refused))
        assert response.status_code == 400
        assert response.json()["error"]["param"] == "messages[2].reasoning_content"
        second = await client.post("/v1/chat/completions", headers=headers, json=completion(messages))
        assert second.status_code == 200, second.text
        assert sent_text(probe.requests[1]) == ["Orchard.", "Noted.", "Name?"]
        assert "Echoed thinking" not in json.dumps(probe.requests[1])
        turns, _ = await stored_turns(app, SESSION)
    assert turns == [["Orchard."], ["Name?"]]


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(conversation("Orchard."), id="reverted"),
        pytest.param(conversation("Orchard.", "Noted.", "Edited.", "Reply.", "Next.", "Reply.", "More."), id="ahead"),
    ],
)
async def test_disagreement_runs_ephemeral_and_leaves_the_session_unchanged(monkeypatch, tmp_path, messages):
    async with face(monkeypatch, tmp_path, replies("Noted.", "Seen.", "Ephemeral.")) as (app, client, probe):
        headers = {"Amplifier-Session-Id": SESSION}
        for request in (conversation("Orchard."), conversation("Orchard.", "Noted.", "Seen?")):
            response = await client.post("/v1/chat/completions", headers=headers, json=completion(request))
            assert response.status_code == 200, response.text
        before = (tmp_path / "sessions" / "sessions" / SESSION / "transcript.jsonl").read_bytes()
        response = await client.post("/v1/chat/completions", headers=headers, json=completion(messages))
        assert response.status_code == 200, response.text
        assert sent_text(probe.requests[2]) == [message["content"] for message in messages]
        turns, _ = await stored_turns(app, SESSION)
    assert turns == [["Orchard."], ["Seen?"]]
    assert (tmp_path / "sessions" / "sessions" / SESSION / "transcript.jsonl").read_bytes() == before


async def test_a_missing_session_with_more_than_one_user_message_runs_ephemeral(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, replies("Ephemeral.")) as (app, client, _):
        response = await client.post(
            "/v1/chat/completions",
            headers={"Amplifier-Session-Id": SESSION},
            json=completion(conversation("One.", "Reply.", "Two.")),
        )
        assert response.status_code == 200, response.text
        assert [record.session_id for record in await app.state.agent.list_sessions()] == []


@pytest.mark.parametrize(
    ("header", "messages", "param"),
    [
        ("Short", conversation("Hello"), "Amplifier-Session-Id"),
        ("UPPERCASE-SESSION", conversation("Hello"), "Amplifier-Session-Id"),
        (SESSION, conversation("Hello", "Hi"), "messages[1].role"),
    ],
    ids=["too-short", "uppercase", "final-assistant"],
)
async def test_session_requests_are_refused_by_field(monkeypatch, tmp_path, header, messages, param):
    async with face(monkeypatch, tmp_path, replies("Never.")) as (app, client, probe):
        response = await client.post(
            "/v1/chat/completions", headers={"Amplifier-Session-Id": header}, json=completion(messages)
        )
        assert response.status_code == 400
        body = response.json()
        http_shapes.error(body)
        assert (body["error"]["code"], body["error"]["param"]) == ("invalid_input", param)
        assert probe.requests == []
        assert await app.state.agent.list_sessions() == []


async def test_a_turn_in_progress_makes_the_session_busy_and_disconnect_keeps_it(monkeypatch, tmp_path):
    script = [{"chunks": ["Waiting"], "block": True}]
    async with face(monkeypatch, tmp_path, script) as (app, client, probe):
        headers = {"Amplifier-Session-Id": SESSION}
        async with client.stream(
            "POST", "/v1/chat/completions", headers=headers, json=completion(conversation("Hello"), stream=True)
        ) as running:
            async for line in running.aiter_lines():
                if line.startswith("data: "):
                    break
            for messages in (conversation("Hello"), conversation("Hello", "Hi", "Again")):
                response = await client.post("/v1/chat/completions", headers=headers, json=completion(messages))
                assert response.status_code == 409
                http_shapes.error(response.json())
                assert response.json()["error"]["code"] == "busy"
        await asyncio.wait_for(probe.settled.wait(), timeout=5)
        await asyncio.sleep(0.2)
        session = await app.state.agent.resume_session(SESSION)
        try:
            assert [record.result.state for record in session.history] == ["cancelled"]
        finally:
            await session.close()
    assert len(probe.requests) == 1


async def test_a_selection_is_applied_with_set_model_before_the_turn(monkeypatch, tmp_path):
    switches = []
    set_model = Session.set_model

    async def observe(self, provider, model, reasoning_effort=None):
        switches.append((self.info.persistence, provider, model))
        await set_model(self, provider, model, reasoning_effort)

    monkeypatch.setattr(Session, "set_model", observe)
    async with face(monkeypatch, tmp_path, replies("One.", "Two.", "Three.", "Four.")) as (app, client, probe):
        headers = {"Amplifier-Session-Id": SESSION}
        sonnet = "anthropic/claude-sonnet-5-5"
        response = await client.post(
            "/v1/chat/completions",
            json=completion(conversation("Ephemeral."), model=sonnet, reasoning_effort="low"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["model"] == sonnet
        messages = conversation("Held.")
        response = await client.post("/v1/chat/completions", headers=headers, json=completion(messages, model=sonnet))
        assert response.status_code == 200, response.text
        _, info = await stored_turns(app, SESSION)
        assert (info.provider, info.model) == ("anthropic", "claude-sonnet-5-5")
        messages = conversation("Held.", "Two.", "Again.")
        response = await client.post("/v1/chat/completions", headers=headers, json=completion(messages, model=sonnet))
        assert response.status_code == 200, response.text
        messages = conversation("Held.", "Two.", "Again.", "Three.", "Back.")
        response = await client.post("/v1/chat/completions", headers=headers, json=completion(messages))
        assert response.status_code == 200, response.text
        _, info = await stored_turns(app, SESSION)
    assert (info.provider, info.model) == ("anthropic", "claude-sonnet-5")
    assert switches == [
        ("ephemeral", "anthropic", "claude-sonnet-5-5"),
        ("durable", "anthropic", "claude-sonnet-5-5"),
        ("durable", "anthropic", "claude-sonnet-5"),
    ]
    assert probe.requests[0]["reasoning_effort"] == "low"


@pytest.mark.parametrize("model", ["anthropic/", "/claude-sonnet-5", "not-a-provider/model", "claude-sonnet-5"])
async def test_unrecognized_model_names_are_refused_by_name(monkeypatch, tmp_path, model):
    async with face(monkeypatch, tmp_path, replies("Never.")) as (app, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            headers={"Amplifier-Session-Id": SESSION},
            json=completion(conversation("Hello"), model=model),
        )
        assert response.status_code == 404
        http_shapes.error(response.json())
        assert response.json()["error"]["code"] == "selector_rejected"
        assert model in response.json()["error"]["message"]
        assert probe.requests == []
        assert await app.state.agent.list_sessions() == []


async def test_a_refused_selection_does_not_keep_the_session_it_would_create(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, replies("Never.")) as (app, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            headers={"Amplifier-Session-Id": SESSION},
            json=completion(conversation("Hello"), model="anthropic/claude-sonnet-5", reasoning_effort="huge"),
        )
        assert response.status_code == 400
        assert (response.json()["error"]["code"], response.json()["error"]["param"]) == (
            "invalid_input",
            "reasoning_effort",
        )
        assert probe.requests == []
        assert await app.state.agent.list_sessions() == []


@pytest.mark.parametrize("stream", [False, True])
async def test_delegated_reasoning_arrives_as_whole_blocks(monkeypatch, stream):
    """A reasoning_final repeats the deltas before it, and stands alone when none streamed."""
    import amplifier_agent as binding
    from amplifier_agent_http import _app

    payloads = [
        ("reasoning_final", binding.ReasoningFinal("Delegated.")),
        ("reasoning_delta", binding.ReasoningDelta("Own ")),
        ("reasoning_delta", binding.ReasoningDelta("thought.")),
        ("reasoning_final", binding.ReasoningFinal("Own thought.")),
        ("reasoning_final", binding.ReasoningFinal("Delegated again.")),
        ("output_delta", binding.OutputDelta([binding.TextPart("Reply")])),
        ("terminal", binding.TurnResult("success", [binding.TextPart("Reply")])),
    ]

    class FixtureTurn:
        async def events(self):
            for sequence, (name, payload) in enumerate(payloads, 1):
                yield binding.Event("turn-events/1", "session-id", "turn-id", sequence, name, payload)

    class FixtureSession:
        async def start_turn(self, turn_input):
            return FixtureTurn()

        async def close(self):
            pass

    class FixtureAgent:
        async def create_session(self, options):
            return FixtureSession()

        async def close(self):
            pass

    async def fixture_agent(options):
        return FixtureAgent()

    monkeypatch.setattr(_app, "create_agent", fixture_agent)
    app = create_app(Settings(TOKEN), AgentOptions(tools=[]))
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}) as client,
    ):
        response = await client.post("/v1/chat/completions", json=completion(conversation("Hello"), stream=stream))
    assert response.status_code == 200, response.text
    if stream:
        frames = [
            line[6:] if line[6:] == "[DONE]" else json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert http_shapes.stream_content(frames) == "Reply"
        assert http_shapes.stream_reasoning(frames) == ["Delegated.", "Own ", "thought.", "Delegated again."]
        roles = [frame["choices"][0]["delta"].get("role") for frame in frames if isinstance(frame, dict)]
        assert roles == ["assistant"] + [None] * (len(roles) - 1)
    else:
        http_shapes.completion(response.json())
        message = response.json()["choices"][0]["message"]
        assert message["reasoning_content"] == "Delegated.Own thought.Delegated again."


async def test_resuming_a_session_writes_only_into_its_own_folder(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, replies("Noted.", "Orchard.")) as (_, client, _):
        headers = {"Amplifier-Session-Id": SESSION}
        for messages in (conversation("Orchard."), conversation("Orchard.", "Noted.", "Name?")):
            response = await client.post("/v1/chat/completions", headers=headers, json=completion(messages))
            assert response.status_code == 200, response.text
    folders = [path.name for path in (tmp_path / "sessions" / "sessions").iterdir() if path.is_dir()]
    assert folders == [SESSION]
