import asyncio
import json
from contextlib import asynccontextmanager
from importlib.resources import files

import httpx
import pytest
from amplifier_agent import (
    AgentError,
    AgentOptions,
    ConversationMessage,
    Session,
    SessionOptions,
    TextPart,
    TurnInput,
    create_agent,
)
from amplifier_agent_http import Settings, create_app
from jsonschema import Draft202012Validator
from openai import AsyncOpenAI

from conformance.fixtures.engine import provision
from conformance.fixtures.http_server import socket_server
from conformance.http.check import check_projection, stream_content

FIXTURES = files("conformance.http")
FIELDS = json.loads((FIXTURES / "fields.json").read_text())
CASES = json.loads((FIXTURES / "cases.json").read_text())


def shape(name, body):
    Draft202012Validator({**FIELDS, "$ref": f"#/$defs/{name}"}).validate(body)


@asynccontextmanager
async def face(monkeypatch, script=None):
    probe = provision(monkeypatch, script or [{"chunks": ["Hello ", "world"], "text": "Hello world"}])
    app = create_app(
        Settings(token="test-token"),
        AgentOptions(
            provider="anthropic", model="claude-sonnet-5", instructions="Server instructions"
        ),
    )
    async with app.router.lifespan_context(app), socket_server(app, lifespan="off") as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            yield app, client, probe


@pytest.mark.parametrize("path", ["/v1/models", "/v1/chat/completions"])
@pytest.mark.parametrize("token", [None, "wrong"])
async def test_authentication_both_endpoints(monkeypatch, path, token):
    async with face(monkeypatch) as (_, client, probe):
        client.headers.clear()
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        response = await client.request(
            "GET" if path.endswith("models") else "POST", path, headers=headers
        )
        assert response.status_code == 401
        shape("error", response.json())
        assert "Supply" in response.json()["error"]["message"]
        assert probe.requests == []




async def test_model_selection_and_fields(monkeypatch):
    async with face(monkeypatch) as (_, client, probe):
        response = await client.get("/v1/models")
        shape("models", response.json())
        assert [model["id"] for model in response.json()["data"]] == ["amplifier"]
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "unknown", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert response.status_code == 404
        shape("error", response.json())
        assert response.json()["error"]["code"] == "selector_rejected"
        assert "unknown" in response.json()["error"]["message"]
        assert "/v1/models" in response.json()["error"]["message"]
        assert probe.requests == []


@pytest.mark.parametrize("case", CASES["requests"], ids=lambda case: case["id"])
async def test_pinned_request_cases(monkeypatch, case):
    admitted = []
    start_turn = Session.start_turn

    async def observe(self, turn_input):
        admitted.append(turn_input)
        return await start_turn(self, turn_input)

    monkeypatch.setattr(Session, "start_turn", observe)
    async with face(monkeypatch) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=case["body"])
        if not case["valid"]:
            assert response.status_code == 400
            shape("error", response.json())
            assert response.json()["error"]["code"] == "invalid_input"
            assert probe.requests == []
            assert admitted == []
            return
        assert response.status_code == 200, response.text
        assert len(probe.requests) == 1
        request = probe.requests[0]
        history = case["projection"]["history"]
        assert len(admitted) == 1 and admitted[0].content == []
        assert [
            {"role": message.role, "content": [{"type": part.type, "text": part.text} for part in message.content]}
            for message in admitted[0].history
        ] == history
        actual = [
            {
                "role": message["role"],
                "content": [
                    {"type": part["type"], "text": part["text"]} for part in message["content"]
                ],
            }
            for message in request["messages"]
            if message["role"] != "system" or message["content"] != "Server instructions"
        ]
        assert actual == history
        assert request["messages"][0]["role"] == "system"
        assert request["messages"][0]["content"] == "Server instructions"


async def test_nonstream_stream_binding_parity_and_request_isolation(monkeypatch):
    async with face(monkeypatch) as (_, client, probe):
        body = {"model": "amplifier", "messages": [{"role": "user", "content": "Hello"}]}
        ordinary = await client.post("/v1/chat/completions", json=body)
        shape("completion", ordinary.json())
        streamed = await client.post("/v1/chat/completions", json={**body, "stream": True})
        frames = [
            json.loads(line[6:]) if line[6:] != "[DONE]" else "[DONE]"
            for line in streamed.text.splitlines()
            if line.startswith("data: ")
        ]
        check_projection(
            frames,
            {
                "deltas": [
                    [{"type": "text", "text": "Hello "}],
                    [{"type": "text", "text": "world"}],
                ],
                "terminal": {"content": [{"type": "text", "text": "Hello world"}]},
            },
        )
        async with await create_agent(
            AgentOptions(provider="anthropic", model="claude-sonnet-5")
        ) as agent:
            async with await agent.create_session(
                SessionOptions(persistence="ephemeral")
            ) as session:
                result = await session.run(TurnInput(
                    [], history=[ConversationMessage("user", [TextPart("Hello")])],
                ))
        assert (
            stream_content(frames)
            == ordinary.json()["choices"][0]["message"]["content"]
            == "".join(part.text for part in result.content)
        )
        assert len(probe.requests) == 3
        assert [(m["role"], m["content"]) for m in probe.requests[0]["messages"]] == [
            (m["role"], m["content"]) for m in probe.requests[1]["messages"]
        ]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("partial", [False, True])
async def test_provider_failures_never_finish_successfully(monkeypatch, stream, partial):
    script = [{"chunks": ["Partial"] if partial else [], "failure": True}]
    async with face(monkeypatch, script) as (_, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "amplifier",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": stream,
            },
        )
        if partial and stream:
            assert response.status_code == 200
            frames = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert stream_content(frames) is None
            body = frames[-1]
            assert "[DONE]" not in response.text
        else:
            assert response.status_code == 502
            body = response.json()
        shape("error", body)
        assert body["error"]["code"] == "provider_failed"
        assert len(body["error"]["message"].split(".")) > 1
        assert probe.active == 0


async def test_session_closes_when_start_turn_rejects(monkeypatch):
    closed = []
    original_close = Session.close

    async def reject(self, input):
        raise AgentError("invalid_input", "input", "Rejected history.", "Supply valid history.")

    async def close(self):
        closed.append(self.info.session_id)
        await original_close(self)

    monkeypatch.setattr(Session, "start_turn", reject)
    monkeypatch.setattr(Session, "close", close)
    async with face(monkeypatch) as (_, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "amplifier", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert response.status_code == 400
        assert len(closed) == 1
        assert probe.requests == []


async def test_unmodified_openai_client_over_socket(monkeypatch):
    provision(monkeypatch, [{"chunks": ["Hello ", "world"], "text": "Hello world"}])
    app = create_app(
        Settings("test-token"), AgentOptions(provider="anthropic", model="claude-sonnet-5")
    )
    async with socket_server(app) as url:
        async with AsyncOpenAI(base_url=f"{url}/v1", api_key="test-token", max_retries=0) as client:
            assert [model.id for model in (await client.models.list()).data] == ["amplifier"]
            reply = await client.chat.completions.create(
                model="amplifier", messages=[{"role": "user", "content": "Hello"}]
            )
            stream = await client.chat.completions.create(
                model="amplifier", messages=[{"role": "user", "content": "Hello"}], stream=True
            )
            async with stream:
                chunks = [chunk async for chunk in stream]
            assert (
                "".join(chunk.choices[0].delta.content or "" for chunk in chunks)
                == reply.choices[0].message.content
                == "Hello world"
            )
            assert chunks[-1].choices[0].finish_reason == "stop"


async def test_disconnect_settles_provider(monkeypatch):
    probe = provision(monkeypatch, [{"chunks": ["Waiting"], "block": True}])
    app = create_app(
        Settings("test-token"), AgentOptions(provider="anthropic", model="claude-sonnet-5")
    )
    async with socket_server(app) as url:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer test-token"}) as client:
            async with client.stream(
                "POST",
                f"{url}/v1/chat/completions",
                json={
                    "model": "amplifier",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        break
            await asyncio.wait_for(probe.settled.wait(), timeout=5)
            assert probe.active == 0
