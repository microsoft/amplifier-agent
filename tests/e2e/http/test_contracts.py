import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from amplifier_agent import (
    Agent,
    AgentError,
    AgentOptions,
    ConversationMessage,
    McpServer,
    Session,
    SessionOptions,
    TextPart,
    TurnInput,
    create_agent,
)
from amplifier_agent_http import Settings, create_app

from conformance.fixtures.engine import provision
from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import KEY_ENV, MODELS, URL_ENV, provider_service
from conformance.http.check import (
    check_projection,
    stream_content,
    valid_shape,
)

MCP_SERVICE = Path(__file__).resolve().parents[3] / "conformance/fixtures/mcp_service.py"


@pytest.fixture
def host_settings(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("AMPLIFIER_AGENT_STORAGE", str(tmp_path / "storage"))
    return path


@asynccontextmanager
async def wire_face(options=None):
    app = create_app(Settings(token="contract-token"), options or AgentOptions())
    async with app.router.lifespan_context(app), socket_server(app, lifespan="off") as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer contract-token"},
        ) as client:
            yield client


@asynccontextmanager
async def face(monkeypatch, script, options=None):
    probe = provision(monkeypatch, script)
    options = options or AgentOptions(provider="anthropic", model="claude-sonnet-5")
    app = create_app(Settings(token="contract-token"), options)
    async with app.router.lifespan_context(app), socket_server(app, lifespan="off") as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer contract-token"},
        ) as client:
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


@pytest.mark.parametrize("source", ["built-in", "mcp"])
@pytest.mark.parametrize("policy", ["allow", "deny"])
@pytest.mark.parametrize("stream", [False, True])
async def test_server_tool_policy_and_reply_only_projection(
    monkeypatch, tmp_path, source, policy, stream
):
    monkeypatch.chdir(tmp_path)
    effect = tmp_path / "effect.txt"
    servers = None
    if source == "built-in":
        tool = {"name": "write_file", "arguments": {"file_path": str(effect), "content": "once"}}
    else:
        servers = [McpServer(
            "ledger", "stdio", command=sys.executable, args=[str(MCP_SERVICE)],
            env={"MCP_LEDGER": str(effect)},
        )]
        tool = {"name": "mcp_ledger_record", "arguments": {"value": "once"}}
    script = [
        {
            "events": [
                {"type": "llm:stream_block_delta", "data": {
                    "block_type": "thinking", "text": "Private reasoning",
                }},
                {"type": "llm:stream_block_end", "data": {"block_type": "thinking"}},
            ],
            "tool": tool,
        },
        {"chunks": ["Final ", "reply"], "text": "Final reply"},
    ]
    options = AgentOptions(
        provider="anthropic", model="claude-sonnet-5", approvals=policy, mcp_servers=servers,
    )
    async with face(monkeypatch, script, options) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=request(stream=stream))
        if policy == "deny":
            assert response.status_code == 403
            assert valid_shape("error", response.json())
            assert response.json()["error"]["code"] == "approval_denied"
            assert not effect.exists()
            assert len(probe.requests) == 1
        else:
            assert response.status_code == 200, response.text
            assert len(probe.requests) == 2
            if source == "built-in":
                assert effect.read_text() == "once"
            else:
                assert [json.loads(line)["value"] for line in effect.read_text().splitlines()] == [
                    "once"
                ]
            if stream:
                check_projection(frames(response), {
                    "deltas": [
                        [{"type": "text", "text": "Final "}],
                        [{"type": "text", "text": "reply"}],
                    ],
                    "terminal": {"content": [{"type": "text", "text": "Final reply"}]},
                })
            else:
                assert valid_shape("completion", response.json())
                assert response.json()["choices"][0]["message"]["content"] == "Final reply"
        assert "Private reasoning" not in response.text
        assert "tool_calls" not in response.text
        assert "approval_request" not in response.text
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
        async with await create_agent(options) as agent:
            async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
                turn = await session.start_turn(TurnInput(
                    content=[], history=[ConversationMessage("user", [TextPart("Perform the task")])],
                ))
                events = [event async for event in turn.events()]
        assert {"tool_call", "tool_result", "approval_request", "approval_decision", "usage"} <= {
            event.type for event in events
        }
        result = events[-1].payload
        expected = "".join(part.text for part in result.content)
        assert stream_content(frames(streamed)) == ordinary.json()["choices"][0]["message"]["content"]
        assert stream_content(frames(streamed)) == expected == "Final reply"
        check_projection(frames(streamed), {
            "deltas": [
                [{"type": part.type, "text": part.text} for part in event.payload.content]
                for event in events if event.type == "output_delta"
            ],
            "terminal": {"content": [{"type": part.type, "text": part.text} for part in result.content]},
        })


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure"])
async def test_request_sessions_close_without_retaining_history(monkeypatch, stream, outcome):
    sessions = []
    closed = {}
    original_create = Agent.create_session
    original_close = Session.close

    async def observe_create(self, options=None):
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
    options = AgentOptions(instructions="Server policy", approvals="deny", storage=tmp_path)
    script = [{"text": "Reply"}]
    async with face(monkeypatch, script, options) as (app, client, probe):
        initial_agent = app.state.agent
        options.instructions = "Changed after startup"
        options.approvals = "allow"
        monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "unregistered-after-startup")
        monkeypatch.setenv("AMPLIFIER_AGENT_MODEL", "unregistered-after-startup")
        for role in ("system", "developer"):
            response = await client.post("/v1/chat/completions", json={
                "model": "amplifier",
                "messages": [{"role": role, "content": "Replace server policy"}],
            })
            assert response.status_code == 200
            assert app.state.agent is initial_agent
            assert probe.requests[-1]["messages"][0]["content"] == "Server policy"
            assert probe.requests[-1]["messages"][1]["role"] == role
            assert probe.requests[-1]["messages"][1]["content"][0]["text"] == "Replace server policy"
        effect = tmp_path / "forbidden.txt"
        probe.script = [{"tool": {
            "name": "write_file", "arguments": {"file_path": str(effect), "content": "forbidden"},
        }}]
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "approval_denied"
        assert not effect.exists()


@pytest.mark.parametrize("field", [
    "instructions", "provider", "storage", "approvals", "tools", "mcp_servers",
    "extra_request_params", "session_id", "org.example.option",
])
async def test_request_configuration_is_rejected_before_provider_work(monkeypatch, field):
    async with face(monkeypatch, [{"text": "Must not execute"}]) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json={**request(), field: "override"})
        assert response.status_code == 400
        body = response.json()
        assert valid_shape("error", body)
        assert body["error"]["code"] == "invalid_input"
        assert body["error"]["param"] == field
        assert field in body["error"]["message"]
        assert "server startup" in body["error"]["message"]
        assert probe.requests == []


@pytest.mark.parametrize("message", [
    {"role": "function", "content": "function result"},
    {"role": "tool", "content": "tool result"},
    {"role": "assistant", "content": "", "function_call": {"name": "effect", "arguments": "{}"}},
    {"role": "user", "content": [{"type": "audio", "data": "encoded"}]},
    {"role": "assistant", "content": [{"type": "text", "text": 42}]},
], ids=["function-role", "tool-role", "function-call", "audio", "nontext-value"])
async def test_unsupported_history_is_rejected_before_provider_work(monkeypatch, message):
    async with face(monkeypatch, [{"text": "Must not execute"}]) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json={
            "model": "amplifier", "messages": [message],
        })
        assert response.status_code == 400
        body = response.json()
        assert valid_shape("error", body)
        assert body["error"]["code"] == "invalid_input"
        assert body["error"]["param"].startswith("messages[0]")
        assert len(body["error"]["message"].split(".")) > 1
        assert probe.requests == []












@pytest.mark.production_only
@pytest.mark.parametrize("layer", ["defaults", "file", "environment", "options"])
async def test_http_startup_obeys_config_precedence(host_settings, monkeypatch, layer):
    recorded = {"anthropic": [], "openai": []}
    options = AgentOptions()
    expected = ("anthropic", "claude-sonnet-5")
    if layer != "defaults":
        expected = ("openai", "gpt-5")
        host_settings.write_text(json.dumps(dict(zip(("provider", "model"), expected))))
    if layer in {"environment", "options"}:
        expected = ("anthropic", "claude-opus-5")
        monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", expected[0])
        monkeypatch.setenv("AMPLIFIER_AGENT_MODEL", expected[1])
    if layer == "options":
        expected = ("openai", "gpt-5-mini")
        options = AgentOptions(provider=expected[0], model=expected[1])
    async with AsyncExitStack() as stack:
        for provider, requests in recorded.items():
            url = await stack.enter_async_context(socket_server(provider_service(provider, requests)))
            monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
            monkeypatch.setenv(URL_ENV[provider], url)
        async with wire_face(options) as client:
            response = await client.post("/v1/chat/completions", json=request())
            assert response.status_code == 200, response.text
    assert len(recorded[expected[0]]) == 1
    assert recorded[expected[0]][0]["model"] == expected[1]
    assert sum(len(requests) for requests in recorded.values()) == 1


@pytest.mark.parametrize("source", ["file", "environment"])
async def test_http_unknown_host_key_names_nearest_remedy(host_settings, monkeypatch, source):
    if source == "file":
        host_settings.write_text('{"modle":"wrong"}')
        name, nearest = "modle", "model"
    else:
        monkeypatch.setenv("AMPLIFIER_AGENT_MODLE", "wrong")
        name, nearest = "AMPLIFIER_AGENT_MODLE", "AMPLIFIER_AGENT_MODEL"
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}]):
            pytest.fail("An invalid host configuration started the HTTP app")
    assert caught.value.code == "invalid_input"
    assert name in caught.value.message and nearest in caught.value.remedy


@pytest.mark.parametrize("value", ["true", "yes", "False", "", 0, 1, [], {}])
async def test_http_ambiguous_host_booleans_fail_before_startup(host_settings, monkeypatch, value):
    host_settings.write_text(json.dumps({"extra_request_params": {"openai": {"store": value}}}))
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}], AgentOptions(provider="openai", model="gpt-5")):
            pytest.fail("An ambiguous host boolean started the HTTP app")
    assert caught.value.code == "invalid_input"
    assert "extra_request_params.openai.store" in caught.value.message
    assert caught.value.remedy


@pytest.mark.production_only
@pytest.mark.parametrize("value,expected", [(False, False), (True, True), ("false", False), ("0", False), ("no", False)])
async def test_http_host_overrides_and_booleans_reach_wire(host_settings, monkeypatch, value, expected):
    host_settings.write_text(json.dumps({"extra_request_params": {
        "openai": {"store": value, "metadata": {"owner": "http-host"}, "org.example.setting": [1, "two"]},
        "anthropic": {"org.example.other": "other-provider"},
    }}))
    recorded = []
    async with socket_server(provider_service("openai", recorded)) as url:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
        monkeypatch.setenv("OPENAI_BASE_URL", url)
        async with wire_face(AgentOptions(provider="openai", model="gpt-5")) as client:
            response = await client.post("/v1/chat/completions", json=request())
            assert response.status_code == 200, response.text
    assert len(recorded) == 1
    assert recorded[0]["store"] is expected
    assert recorded[0]["metadata"] == {"owner": "http-host"}
    assert recorded[0]["org.example.setting"] == [1, "two"]
    assert "org.example.other" not in recorded[0]
    assert "previous_response_id" not in recorded[0]


@pytest.mark.production_only
async def test_http_host_options_and_connections_are_snapshotted(host_settings, monkeypatch):
    host_settings.write_text('{"extra_request_params":{"openai":{"metadata":{"owner":"original"}}}}')
    recorded = []
    options = AgentOptions(provider="openai", model="gpt-5", instructions="Original policy")
    async with socket_server(provider_service("openai", recorded)) as url:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
        monkeypatch.setenv("OPENAI_BASE_URL", url)
        async with wire_face(options) as client:
            host_settings.write_text('{"modle":"invalid-after-startup"}')
            options.model = "gpt-5-mini"
            options.instructions = "Mutated policy"
            monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", "invalid-after-startup")
            monkeypatch.setenv("OPENAI_API_KEY", "mutated-key")
            monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1")
            for text in ("First", "Second"):
                response = await client.post("/v1/chat/completions", json=request(text=text))
                assert response.status_code == 200, response.text
    assert len(recorded) == 2
    assert all(body["model"] == "gpt-5" for body in recorded)
    assert all(body["instructions"] == "Original policy" for body in recorded)
    assert all(body["metadata"] == {"owner": "original"} for body in recorded)


@pytest.mark.parametrize("key", ["previous_response_id", "conversation", "input"])
async def test_http_host_overrides_cannot_replace_conversation_semantics(host_settings, monkeypatch, key):
    host_settings.write_text(json.dumps({"extra_request_params": {"openai": {key: "remote-state"}}}))
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}], AgentOptions(provider="openai", model="gpt-5")):
            pytest.fail("A conversation override started the HTTP app")
    assert caught.value.code == "invalid_input"
    assert key in caught.value.message and caught.value.remedy


@pytest.mark.production_only
@pytest.mark.parametrize("retain", [False, True])
async def test_http_history_is_complete_even_with_retention_opt_in(host_settings, monkeypatch, retain):
    if retain:
        host_settings.write_text('{"extra_request_params":{"openai":{"store":true}}}')
    recorded = []
    async with socket_server(provider_service("openai", recorded)) as url:
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
        monkeypatch.setenv("OPENAI_BASE_URL", url)
        async with wire_face(AgentOptions(provider="openai", model="gpt-5")) as client:
            body = request(text="First question")
            first = await client.post("/v1/chat/completions", json=body)
            assert first.status_code == 200, first.text
            body["messages"].extend([
                {"role": "assistant", "content": first.json()["choices"][0]["message"]["content"]},
                {"role": "user", "content": "Second question"},
            ])
            second = await client.post("/v1/chat/completions", json=body)
            assert second.status_code == 200, second.text
    assert len(recorded) == 2
    assert all(body["store"] is retain for body in recorded)
    assert all("previous_response_id" not in body and "conversation" not in body for body in recorded)
    assert [message["role"] for message in recorded[1]["input"]] == ["user", "assistant", "user"]
    assert [message["content"][0]["text"] for message in recorded[1]["input"]] == [
        "First question", "Wire reply", "Second question",
    ]


@pytest.mark.production_only
@pytest.mark.parametrize("provider", MODELS)
async def test_http_reasoning_replays_within_one_turn_and_not_between_requests(
    host_settings, monkeypatch, tmp_path, provider
):
    input_path = tmp_path / "input.txt"
    input_path.write_text("read result")
    recorded = []
    async with socket_server(provider_service(
        provider, recorded, tool="read_file", tool_arguments={"file_path": str(input_path)},
        reasoning=True, late_signature=True,
    )) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with wire_face(AgentOptions(provider=provider, model=MODELS[provider], approvals="allow")) as client:
            for text in ("First private request", "Second private request"):
                response = await client.post("/v1/chat/completions", json=request(text=text, stream=True))
                assert response.status_code == 200, response.text
                assert stream_content(frames(response)) == "Wire reply"
    assert len(recorded) == 4
    marker = {"anthropic": "fixture-signature", "openai": "opaque-fixture-reasoning", "gemini": "c2lnbmF0dXJl"}[provider]
    assert marker in json.dumps(recorded[1])
    assert "First private request" in json.dumps(recorded[1])
    assert "read result" in json.dumps(recorded[1])
    assert marker not in json.dumps(recorded[2])
    assert "First private request" not in json.dumps(recorded[2])
    assert "Second private request" in json.dumps(recorded[2])
    assert all("previous_response_id" not in body and "conversation" not in body for body in recorded)
    if provider == "openai":
        assert all(body["store"] is False for body in recorded)


@pytest.mark.production_only
@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("substituted", [False, True])
async def test_http_primary_model_is_honored_or_refused(host_settings, monkeypatch, provider, substituted):
    actual = {"anthropic": "claude-opus-5", "openai": "gpt-5-mini", "gemini": "gemini-unrequested-model"}[provider]
    recorded = []
    async with socket_server(provider_service(
        provider, recorded, reported_model=actual if substituted else MODELS[provider],
    )) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with wire_face(AgentOptions(provider=provider, model=MODELS[provider])) as client:
            response = await client.post("/v1/chat/completions", json=request())
    assert len(recorded) == 1
    if substituted:
        assert response.status_code == 404, response.text
        assert valid_shape("error", response.json())
        assert response.json()["error"]["code"] == "selector_rejected"
    else:
        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == "Wire reply"


async def test_http_delegation_cannot_exceed_server_ceiling(host_settings, monkeypatch):
    script = [{"tool": {"name": "delegate", "arguments": {
        "instruction": "Use a more expensive model", "model": "claude-opus-5",
    }}}]
    async with face(monkeypatch, script, AgentOptions(
        provider="anthropic", model="claude-sonnet-5", approvals="allow",
    )) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "selector_rejected"
        assert len(probe.requests) == 1


@pytest.mark.parametrize("source,providers", [
    ("options", ["anthropic", "openai"]), ("file", ["anthropic", "openai"]),
    ("environment", ["anthropic", "openai"]), ("options", ["github-copilot", "anthropic"]),
])
async def test_http_startup_refuses_multiple_providers(host_settings, monkeypatch, source, providers):
    options = AgentOptions()
    if source == "options":
        options.provider = providers
    elif source == "file":
        host_settings.write_text(json.dumps({"provider": providers}))
    else:
        monkeypatch.setenv("AMPLIFIER_AGENT_PROVIDER", json.dumps(providers))
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}], options):
            pytest.fail("Multiple providers started the HTTP app")
    assert caught.value.code in {"invalid_input", "selector_rejected"}
    assert "provider" in caught.value.message.lower()
    assert caught.value.remedy


@pytest.mark.parametrize("source,workspace,valid", [
    ("file", "a", True), ("file", "a" * 64, True),
    ("file", "../escape", False), ("file", "Uppercase", False),
    ("file", "-start", False), ("file", "a" * 65, False),
    ("environment", "a" * 64, True), ("environment", "../escape", False),
])
async def test_http_workspace_slug_validation(host_settings, monkeypatch, source, workspace, valid):
    if source == "file":
        host_settings.write_text(json.dumps({"workspace": workspace}))
    else:
        monkeypatch.setenv("AMPLIFIER_AGENT_WORKSPACE", workspace)
    if valid:
        async with face(monkeypatch, [{"text": "Reply"}]) as (_, client, probe):
            response = await client.post("/v1/chat/completions", json=request())
            assert response.status_code == 200
            assert len(probe.requests) == 1
    else:
        with pytest.raises(AgentError) as caught:
            async with face(monkeypatch, [{"text": "Must not start"}]):
                pytest.fail("An invalid workspace started the HTTP app")
        assert caught.value.code == "invalid_input"
        assert "workspace" in caught.value.message and caught.value.remedy


@pytest.mark.parametrize("key", ["bundles", "modules", "hooks", "orchestrator", "routing", "modes", "recipes"])
async def test_http_host_configuration_refuses_excluded_controls(host_settings, monkeypatch, key):
    host_settings.write_text(json.dumps({key: {}}))
    with pytest.raises(AgentError) as caught:
        async with face(monkeypatch, [{"text": "Must not start"}]):
            pytest.fail("An excluded host control started the HTTP app")
    assert caught.value.code == "invalid_input"
    assert key in caught.value.message and caught.value.remedy


async def test_http_all_registered_host_keys_are_accepted(host_settings, monkeypatch, tmp_path):
    host_settings.write_text(json.dumps({
        "provider": "anthropic", "model": "claude-sonnet-5", "storage": str(tmp_path / "configured"),
        "workspace": "configured-workspace", "extra_request_params": {"anthropic": {}},
    }))
    monkeypatch.delenv("AMPLIFIER_AGENT_STORAGE")
    async with face(monkeypatch, [{"text": "Configured"}], AgentOptions()) as (_, client, probe):
        response = await client.post("/v1/chat/completions", json=request())
        assert response.status_code == 200
        assert len(probe.requests) == 1
