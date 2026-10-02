import asyncio
from contextlib import asynccontextmanager
import json

from amplifier_agent import (
    Agent,
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
import httpx
import pytest

from tests.support import http_shapes
from tests.support.engine import provision
from tests.support.http_server import socket_server
from tests.support.http_shapes import check_projection, stream_content
from tests.support.provider_services import provider_service
from tests.support.scripted_provider import ScriptedProvider


@asynccontextmanager
async def wire_face(options=None):
    app = create_app(Settings(token="contract-token"), options or AgentOptions(tools=[]))
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer contract-token"},
        ) as client,
    ):
        yield client


@asynccontextmanager
async def face(monkeypatch, script, options=None):
    probe = provision(monkeypatch, script)
    options = options or AgentOptions(provider="anthropic", model="claude-sonnet-5", tools=[])
    app = create_app(Settings(token="contract-token"), options)
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer contract-token"},
        ) as client,
    ):
        yield app, client, probe


def frames(response):
    return [
        line[6:] if line[6:] == "[DONE]" else json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def request(*, stream=False, text="Perform the task"):
    return {
        "model": "amplifier",
        "messages": [{"role": "user", "content": text}],
        "stream": stream,
    }


@pytest.mark.parametrize("policy", ["allow", "deny"])
@pytest.mark.parametrize("stream", [False, True])
async def test_server_tool_policy_and_reply_only_projection(monkeypatch, tmp_path, policy, stream):
    monkeypatch.chdir(tmp_path)
    effect = tmp_path / "effect.txt"
    tool = {"name": "write_file", "arguments": {"file_path": str(effect), "content": "once"}}
    script = [
        {
            "events": [
                {
                    "type": "llm:stream_block_delta",
                    "data": {
                        "block_type": "thinking",
                        "text": "Private reasoning",
                    },
                },
                {"type": "llm:stream_block_end", "data": {"block_type": "thinking"}},
            ],
            "tool": tool,
        },
        {"chunks": ["Final ", "reply"], "text": "Final reply"},
    ]
    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        approvals=policy,
    )
    async with face(monkeypatch, script, options) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=request(stream=stream))
        if policy == "deny":
            assert response.status_code == 403
            http_shapes.error(response.json())
            assert response.json()["error"]["code"] == "approval_denied"
            assert not effect.exists()
            assert len(probe.requests) == 1
        else:
            assert response.status_code == 200, response.text
            assert len(probe.requests) == 2
            assert effect.read_text() == "once"
            if stream:
                check_projection(
                    frames(response),
                    {
                        "deltas": [
                            [{"type": "text", "text": "Final "}],
                            [{"type": "text", "text": "reply"}],
                        ],
                        "terminal": {"content": [{"type": "text", "text": "Final reply"}]},
                    },
                )
            else:
                http_shapes.completion(response.json())
                assert response.json()["choices"][0]["message"]["content"] == "Final reply"
        assert "Private reasoning" not in response.text
        assert "tool_calls" not in response.text
        assert "approval_request" not in response.text
        assert probe.active == 0


async def test_server_with_tools_and_no_policy_refuses_to_start(monkeypatch):
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}], AgentOptions()):
            pytest.fail("An agent with tools and no approval policy started the HTTP app")
    assert caught.value.code == "approval_unavailable"
    assert "AMPLIFIER_AGENT_APPROVALS" in caught.value.remedy
    assert "tools=[]" in caught.value.remedy


@pytest.mark.parametrize("policy", ["allow", "deny"])
async def test_ambient_policy_governs_server_tools(monkeypatch, tmp_path, policy):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVALS", policy)
    effect = tmp_path / "effect.txt"
    script = [
        {"tool": {"name": "write_file", "arguments": {"file_path": str(effect), "content": "once"}}},
        {"chunks": ["Final reply"], "text": "Final reply"},
    ]
    async with face(monkeypatch, script, AgentOptions()) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=request())
        if policy == "deny":
            assert response.status_code == 403
            http_shapes.error(response.json())
            assert response.json()["error"]["code"] == "approval_denied"
            assert not effect.exists()
        else:
            assert response.status_code == 200, response.text
            assert response.json()["choices"][0]["message"]["content"] == "Final reply"
            assert effect.read_text() == "once"
        assert probe.active == 0


async def test_tool_turn_matches_binding_terminal_and_event_boundaries(monkeypatch, tmp_path):
    effect = tmp_path / "input.txt"
    effect.write_text("tool result")
    script = [
        {"tool": {"name": "read_file", "arguments": {"file_path": str(effect)}}},
        {"chunks": ["Final ", "reply"], "text": "Final reply"},
    ]
    options = AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow")
    async with face(monkeypatch, script, options) as (_, client, _):
        ordinary = await client.post("/v1/chat/completions", json=request())
        streamed = await client.post("/v1/chat/completions", json=request(stream=True))
        async with (
            await create_agent(options) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            turn = await session.start_turn(
                TurnInput(
                    content=[],
                    history=[ConversationMessage("user", [TextPart("Perform the task")])],
                )
            )
            events = [event async for event in turn.events()]
        assert {"tool_call", "tool_result", "approval_request", "approval_decision", "usage"} <= {
            event.type for event in events
        }
        result = events[-1].payload
        expected = "".join(part.text for part in result.content)
        assert stream_content(frames(streamed)) == ordinary.json()["choices"][0]["message"]["content"]
        assert stream_content(frames(streamed)) == expected == "Final reply"
        check_projection(
            frames(streamed),
            {
                "deltas": [
                    [{"type": part.type, "text": part.text} for part in event.payload.content]
                    for event in events
                    if event.type == "output_delta"
                ],
                "terminal": {"content": [{"type": part.type, "text": part.text} for part in result.content]},
            },
        )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure"])
async def test_request_sessions_close_without_retaining_history(monkeypatch, stream, outcome):
    sessions = []
    closed = {}
    original_create = Agent.create_session
    original_close = Session.close

    async def observe_create(self, options=None):
        assert options is not None
        assert options.persistence == "ephemeral"
        session = await original_create(self, options)
        sessions.append(session)
        closed[session] = asyncio.Event()
        return session

    async def observe_close(self):
        await original_close(self)
        closed[self].set()

    monkeypatch.setattr(Agent, "create_session", observe_create)
    monkeypatch.setattr(Session, "close", observe_close)
    script = [{"text": "Reply", "chunks": ["Reply"], "failure": outcome == "failure"}]
    async with face(monkeypatch, script) as (app, client, probe):
        for text in ("first isolated request", "second isolated request"):
            response = await client.post("/v1/chat/completions", json=request(stream=stream, text=text))
            if outcome == "success":
                assert response.status_code == 200
            elif stream:
                assert stream_content(frames(response)) is None
            else:
                assert response.status_code == 502
            await asyncio.wait_for(closed[sessions[-1]].wait(), 5)
            with pytest.raises(AgentError) as caught:
                await sessions[-1].run(TurnInput([TextPart("must remain closed")]))
            assert caught.value.code == "closed"
            assert await app.state.agent.list_sessions() == []
        assert len(sessions) == 2
        assert sessions[0] is not sessions[1]
        assert len(probe.requests) == 2
        assert "first isolated request" not in json.dumps(probe.requests[1]["messages"])


async def test_startup_configuration_is_immutable_across_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AMPLIFIER_AGENT_MODEL", "claude-sonnet-5")
    options = AgentOptions(model="claude-sonnet-5", instructions="Server policy", approvals="deny", storage=tmp_path)
    script = [{"text": "Reply"}]
    models = []
    complete = ScriptedProvider.complete

    async def observe(self, request, **kwargs):
        models.append(kwargs["model"])
        return await complete(self, request, **kwargs)

    monkeypatch.setattr(ScriptedProvider, "complete", observe)
    async with face(monkeypatch, script, options) as (app, client, probe):
        initial_agent = app.state.agent
        options.model = "claude-opus-5"
        options.instructions = "Changed after startup"
        options.approvals = "allow"
        monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "unregistered-after-startup")
        monkeypatch.setenv("AMPLIFIER_AGENT_MODEL", "unregistered-after-startup")
        for role in ("system", "developer"):
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "amplifier",
                    "messages": [{"role": role, "content": "Replace server policy"}],
                },
            )
            assert response.status_code == 200
            assert app.state.agent is initial_agent
            assert probe.requests[-1]["messages"][0]["content"] == "Server policy"
            assert probe.requests[-1]["messages"][1]["role"] == role
            assert probe.requests[-1]["messages"][1]["content"][0]["text"] == "Replace server policy"
        effect = tmp_path / "forbidden.txt"
        probe.script = [
            {
                "tool": {
                    "name": "write_file",
                    "arguments": {"file_path": str(effect), "content": "forbidden"},
                }
            }
        ]
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "approval_denied"
        assert not effect.exists()
    assert models == ["claude-sonnet-5"] * 3


@pytest.mark.parametrize(
    "field",
    [
        "instructions",
        "provider",
        "storage",
        "approvals",
        "tools",
        "mcp_servers",
        "extra_request_params",
        "session_id",
        "org.example.option",
    ],
)
async def test_request_configuration_is_rejected_before_provider_work(monkeypatch, field):
    async with face(monkeypatch, [{"text": "Must not execute"}]) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json={**request(), field: "override"})
        assert response.status_code == 400
        body = response.json()
        http_shapes.error(body)
        assert body["error"]["code"] == "invalid_input"
        assert body["error"]["param"] == field
        assert field in body["error"]["message"]
        assert "server startup" in body["error"]["message"]
        assert probe.requests == []


@pytest.mark.parametrize(
    "message",
    [
        {"role": "function", "content": "function result"},
        {"role": "tool", "content": "tool result"},
        {"role": "assistant", "content": "", "function_call": {"name": "effect", "arguments": "{}"}},
        {"role": "user", "content": [{"type": "audio", "data": "encoded"}]},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": 42}]},
    ],
    ids=["function-role", "tool-role", "function-call", "audio", "image", "nontext-value"],
)
async def test_unsupported_history_is_rejected_before_provider_work(monkeypatch, message):
    async with face(monkeypatch, [{"text": "Must not execute"}]) as (_, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "amplifier",
                "messages": [message],
            },
        )
        assert response.status_code == 400
        body = response.json()
        http_shapes.error(body)
        assert body["error"]["code"] == "invalid_input"
        assert body["error"]["param"].startswith("messages[0]")
        assert len(body["error"]["message"].split(".")) > 1
        assert probe.requests == []


async def test_invalid_host_configuration_fails_startup_with_the_agent_error(monkeypatch, isolated_host):
    isolated_host.write_text('{"modle":"wrong"}')
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}]):
            pytest.fail("An invalid host configuration started the HTTP app")
    assert caught.value.code == "invalid_input"
    assert "modle" in caught.value.message
    assert "model" in caught.value.remedy


async def test_client_history_reaches_the_wire_whole_and_nothing_carries_between_requests(monkeypatch):
    recorded = []
    async with socket_server(provider_service("openai", recorded, reasoning=True)) as url:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
        monkeypatch.setenv("OPENAI_BASE_URL", url)
        async with wire_face(AgentOptions(provider="openai", model="gpt-5", tools=[])) as client:
            body = request(text="First question")
            first = await client.post("/v1/chat/completions", json=body)
            assert first.status_code == 200, first.text
            body["messages"].extend(
                [
                    {"role": "assistant", "content": first.json()["choices"][0]["message"]["content"]},
                    {"role": "user", "content": "Second question"},
                ]
            )
            second = await client.post("/v1/chat/completions", json=body)
            assert second.status_code == 200, second.text
    assert len(recorded) == 2
    assert "opaque-fixture-reasoning" not in json.dumps(recorded[1])
    assert all("previous_response_id" not in body and "conversation" not in body for body in recorded)
    assert [message["role"] for message in recorded[1]["input"]] == ["user", "assistant", "user"]
    assert [message["content"][0]["text"] for message in recorded[1]["input"]] == [
        "First question",
        "Wire reply",
        "Second question",
    ]


async def test_http_delegation_cannot_exceed_server_ceiling(monkeypatch):
    script = [
        {
            "tool": {
                "name": "delegate",
                "arguments": {
                    "instruction": "Use a more expensive model",
                    "model": "claude-opus-5",
                },
            }
        }
    ]
    async with face(
        monkeypatch,
        script,
        AgentOptions(
            provider="anthropic",
            model="claude-sonnet-5",
            approvals="allow",
        ),
    ) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "selector_rejected"
        assert len(probe.requests) == 1


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def image_message(role="user", media_type="image/png"):
    return {
        "role": role,
        "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{PNG}"}},
        ],
    }


async def test_data_url_image_reaches_the_provider_as_an_image_block(monkeypatch):
    async with face(monkeypatch, [{"text": "Seen"}]) as (_, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "amplifier", "messages": [image_message()]},
        )
        assert response.status_code == 200, response.text
        http_shapes.completion(response.json())
    message = probe.requests[0]["messages"][-1]
    assert message["role"] == "user"
    assert [{key: part.get(key) for key in ("type", "text", "source")} for part in message["content"]] == [
        {"type": "text", "text": "Describe this", "source": None},
        {"type": "image", "text": None, "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
    ]


async def test_image_outside_user_messages_is_rejected_before_provider_work(monkeypatch):
    async with face(monkeypatch, [{"text": "Must not execute"}]) as (_, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "amplifier", "messages": [image_message("assistant")]},
        )
        assert response.status_code == 400
        body = response.json()
        http_shapes.error(body)
        assert body["error"]["code"] == "invalid_input"
        assert body["error"]["param"].startswith("messages[0]")
        assert probe.requests == []


@pytest.mark.parametrize("detection", ["reported", "rejected"])
async def test_image_unsupported_is_a_client_error(monkeypatch, detection):
    script = [{"reject_images": True, "text": "Unreachable"}]
    async with face(monkeypatch, script) as (_, client, probe):
        if detection == "reported":
            probe.model_capabilities = ["tools", "streaming"]
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "amplifier", "messages": [image_message()]},
        )
        assert response.status_code == 400, response.text
        body = response.json()
        http_shapes.error(body)
        assert body["error"]["code"] == "image_unsupported"
        assert len(body["error"]["message"].split(".")) > 2
        assert len(probe.requests) == (0 if detection == "reported" else 1)


@pytest.mark.parametrize(("media_type", "data"), [("image/bmp", PNG), ("image/png", "not base64!!")])
async def test_agent_refusal_of_an_image_names_its_request_param(monkeypatch, media_type, data):
    message = image_message()
    message["content"][1]["image_url"]["url"] = f"data:{media_type};base64,{data}"
    async with face(monkeypatch, [{"text": "Must not execute"}]) as (_, client, probe):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "amplifier", "messages": [{"role": "user", "content": "Earlier"}, message]},
        )
        assert response.status_code == 400, response.text
        body = response.json()
        http_shapes.error(body)
        assert body["error"]["code"] == "invalid_input"
        assert body["error"]["param"] == "messages[1].content[1].image_url.url"
        assert probe.requests == []
