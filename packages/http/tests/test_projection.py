import copy
import json

import amplifier_agent as binding
import httpx
import pytest
from amplifier_agent import TextPart
from amplifier_agent_http import Settings, create_app

from conformance.fixtures.http_server import socket_server
from conformance.http.check import CASES, check_fixtures, check_projection, valid_shape


def frames(response):
    return [line[6:] if line[6:] == "[DONE]" else json.loads(line[6:])
            for line in response.text.splitlines() if line.startswith("data: ")]


def request(*, stream=False):
    return {"model": "amplifier", "messages": [{"role": "user", "content": "Reply"}], "stream": stream}


def test_service_settings():
    settings = Settings.from_environment({"AMPLIFIER_AGENT_FACE_TOKEN": "token"})
    assert settings.bind == "127.0.0.1"
    assert settings.port == 9099
    assert settings.model == "amplifier"
    for env in (
        {},
        {"AMPLIFIER_AGENT_FACE_TOKEN": " "},
        {"AMPLIFIER_AGENT_FACE_TOKEN": "token", "AMPLIFIER_AGENT_FACE_PORT": "invalid"},
    ):
        with pytest.raises(ValueError):
            Settings.from_environment(env)



def test_launcher_uses_loopback_default(monkeypatch):
    import uvicorn
    from amplifier_agent_http.__main__ import main

    monkeypatch.setenv("AMPLIFIER_AGENT_FACE_TOKEN", "contract-token")
    monkeypatch.delenv("AMPLIFIER_AGENT_FACE_BIND", raising=False)
    monkeypatch.delenv("AMPLIFIER_AGENT_FACE_PORT", raising=False)
    launches = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launches.append(kwargs))
    main()
    assert launches == [{"host": "127.0.0.1", "port": 9099}]



def test_http_fixture_validators_discriminate_response_mutations():
    result = check_fixtures()
    assert result["requests"] == len(CASES["requests"])
    assert result["response_mutants"] == 10
    assert result["projection_mutants"] == 2



def test_http_shapes_refuse_extra_fields_at_every_object():
    from amplifier_agent_http._projection import InvalidRequest, project_request

    def object_paths(value, path=()):
        if isinstance(value, dict):
            yield path
            for key, child in value.items():
                yield from object_paths(child, (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from object_paths(child, (*path, index))

    shapes = [
        ("request", case["body"]) for case in CASES["requests"] if case["valid"]
    ] + [
        ("error" if name.endswith("_error") else name, body)
        for name, body in CASES["responses"].items()
    ]
    for shape_name, body in shapes:
        assert valid_shape(shape_name, body)
        for path in object_paths(body):
            mutant = copy.deepcopy(body)
            target = mutant
            for key in path:
                target = target[key]
            target["org.example.extra"] = "unsupported"
            assert not valid_shape(shape_name, mutant), (shape_name, path)
            if shape_name == "request":
                with pytest.raises(InvalidRequest):
                    project_request(mutant)



@pytest.mark.parametrize("stream", [False, True])
async def test_projection_fixture_drops_every_non_reply_event(monkeypatch, stream):
    from amplifier_agent_http import _app

    usage = binding.Usage([binding.UsageEntry("anthropic", "claude-sonnet-5", tokens_in=3)])
    result = binding.TurnResult("success", [TextPart("Reply")], usage=usage)
    call = binding.ToolCall("call-id", "read_file", "built-in", {"file_path": "fixture"})
    payloads = [
        ("turn_started", binding.TurnStarted("fresh", binding.Selection("anthropic", "claude-sonnet-5"))),
        ("reasoning_delta", binding.ReasoningDelta("Private reasoning")),
        ("reasoning_final", binding.ReasoningFinal("Private reasoning")),
        ("tool_call", binding.ToolCallEvent(call)),
        ("approval_request", binding.ApprovalRequestEvent(binding.ApprovalRequest("request-id", "Read fixture"))),
        ("approval_decision", binding.ApprovalDecision(binding.ApprovalResolution("request-id", "allow"))),
        ("tool_result", binding.ToolResultEvent(binding.ToolResolution("call-id", "completed", "Tool output"))),
        ("progress", binding.Progress({"completed": 1, "total": 2})),
        ("usage", binding.UsageEvent(usage)),
        ("output_delta", binding.OutputDelta([TextPart("Reply")])),
        ("terminal", result),
    ]
    closed = []

    class FixtureTurn:
        async def events(self):
            for sequence, (name, payload) in enumerate(payloads, 1):
                yield binding.Event("turn-events/1", "session-id", "turn-id", sequence, name, payload)

    class FixtureSession:
        async def start_turn(self, turn_input):
            assert turn_input.content == []
            return FixtureTurn()

        async def close(self):
            closed.append("session")

    class FixtureAgent:
        async def create_session(self, options):
            assert options.persistence == "ephemeral"
            return FixtureSession()

        async def close(self):
            closed.append("agent")

    async def fixture_agent(options):
        return FixtureAgent()

    monkeypatch.setattr(_app, "create_agent", fixture_agent)
    app = create_app(Settings("contract-token"))
    async with app.router.lifespan_context(app), socket_server(app, lifespan="off") as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer contract-token"},
        ) as client:
            response = await client.post("/v1/chat/completions", json=request(stream=stream))
        assert closed == ["session"]
        assert response.status_code == 200
        if stream:
            check_projection(frames(response), {
                "deltas": [[{"type": "text", "text": "Reply"}]],
                "terminal": {"content": [{"type": "text", "text": "Reply"}]},
            })
        else:
            assert valid_shape("completion", response.json())
            assert response.json()["choices"][0]["message"]["content"] == "Reply"
        for excluded in ("Private reasoning", "Tool output", "read_file", "request-id", "session-id", "tokens_in"):
            assert excluded not in response.text
    assert closed == ["session", "agent"]
