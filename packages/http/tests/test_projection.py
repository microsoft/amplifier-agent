import copy
from decimal import Decimal
import json
import os

import amplifier_agent as binding
from amplifier_agent import TextPart
from amplifier_agent_http import Settings, create_app
from amplifier_agent_http._projection import InvalidRequestError, project_request, project_usage, request_param
import httpx
import pytest

from tests.support import http_shapes
from tests.support.engine import provision
from tests.support.http_server import socket_server
from tests.support.http_shapes import CASES, check_projection


def frames(response):
    return [
        line[6:] if line[6:] == "[DONE]" else json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def request(*, stream=False):
    return {"model": "amplifier", "messages": [{"role": "user", "content": "Reply"}], "stream": stream}


def test_service_settings():
    settings = Settings.from_environment({"AMPLIFIER_AGENT_FACE_TOKEN": "token"})
    assert settings.bind == "127.0.0.1"
    assert settings.port == 9099
    assert settings.model == "amplifier"
    for env, message in (
        ({}, r"AMPLIFIER_AGENT_FACE_TOKEN to a nonempty bearer token"),
        ({"AMPLIFIER_AGENT_FACE_TOKEN": " "}, r"AMPLIFIER_AGENT_FACE_TOKEN to a nonempty bearer token"),
        (
            {"AMPLIFIER_AGENT_FACE_TOKEN": "token", "AMPLIFIER_AGENT_FACE_PORT": "invalid"},
            r"AMPLIFIER_AGENT_FACE_PORT to an integer from 1 to 65535",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            Settings.from_environment(env)


def launcher_host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    host = tmp_path / "host.json"
    host.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))
    monkeypatch.setenv("AMPLIFIER_AGENT_FACE_TOKEN", "contract-token")
    return host


def test_launcher_uses_loopback_default(monkeypatch, tmp_path):
    from amplifier_agent_http.__main__ import main
    import uvicorn

    launcher_host(monkeypatch, tmp_path)
    monkeypatch.setenv("AMPLIFIER_AGENT_APPROVALS", "deny")
    launches = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launches.append(kwargs))
    main()
    assert launches == [{"host": "127.0.0.1", "port": 9099}]


def test_launcher_refuses_tools_without_policy(monkeypatch, tmp_path, capsys):
    from amplifier_agent_http.__main__ import main
    import uvicorn

    launcher_host(monkeypatch, tmp_path)
    launches = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launches.append(kwargs))
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert launches == []
    error = capsys.readouterr().err
    assert "no approval policy" in error
    assert "Set AMPLIFIER_AGENT_APPROVALS to 'allow' or 'deny'" in error
    assert "Traceback" not in error


def test_launcher_accepts_file_policy(monkeypatch, tmp_path):
    from amplifier_agent_http.__main__ import main
    import uvicorn

    launcher_host(monkeypatch, tmp_path).write_text('{"approvals": "allow"}')
    launches = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launches.append(kwargs))
    main()
    assert len(launches) == 1


async def test_requests_refuse_extension_fields_at_every_object(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AMPLIFIER_AGENT_STORAGE", str(tmp_path / "storage"))
    probe = provision(monkeypatch, [{"text": "Must not execute"}])

    def object_paths(value, path=()):
        if isinstance(value, dict):
            yield path
            for key, child in value.items():
                yield from object_paths(child, (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from object_paths(child, (*path, index))

    def param(path):
        field = "".join(f"[{key}]" if isinstance(key, int) else f".{key}" for key in path).removeprefix(".")
        return f"{field}.org.example.extra" if field else "org.example.extra"

    app = create_app(
        Settings("contract-token"), binding.AgentOptions(provider="anthropic", model="claude-sonnet-5", tools=[])
    )
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer contract-token"}) as client,
    ):
        for case in CASES["requests"]:
            if not case["valid"]:
                continue
            for path in object_paths(case["body"]):
                extended = copy.deepcopy(case["body"])
                target = extended
                for key in path:
                    target = target[key]
                target["org.example.extra"] = "unsupported"
                response = await client.post("/v1/chat/completions", json=extended)
                assert response.status_code == 400, (case["id"], path)
                body = response.json()
                http_shapes.error(body)
                assert body["error"]["code"] == "invalid_input"
                assert body["error"]["param"] == param(path)
    assert probe.requests == []


def entry(**counters):
    return binding.UsageEntry("anthropic", "claude-sonnet-5", **counters)


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        pytest.param(
            [
                entry(
                    tokens_in=100,
                    tokens_out=10,
                    cache_read_tokens=80,
                    cache_write_tokens=5,
                    cost={"USD": Decimal("0.5")},
                )
            ],
            {
                "prompt_tokens": 105,
                "completion_tokens": 10,
                "total_tokens": 115,
                "prompt_tokens_details": {"cached_tokens": 80},
                "cost_usd": "0.5",
            },
            id="cache-read-inside-prompt-cache-write-added",
        ),
        pytest.param(
            [entry(tokens_in=100, tokens_out=10, cache_read_tokens=0)],
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
            id="missing-cache-write-adds-nothing-missing-cost-omitted",
        ),
        pytest.param(
            [entry(tokens_in=100, tokens_out=10, cache_write_tokens=3, cost={"USD": Decimal("0.01")})],
            {"prompt_tokens": 103, "completion_tokens": 10, "total_tokens": 113, "cost_usd": "0.01"},
            id="missing-cache-read-omits-details",
        ),
        pytest.param(
            [
                entry(
                    tokens_in=10, tokens_out=1, cache_read_tokens=4, cache_write_tokens=2, cost={"USD": Decimal("0.1")}
                ),
                binding.UsageEntry(
                    "openai",
                    "gpt-5.6-sol",
                    tokens_in=20,
                    tokens_out=3,
                    cache_read_tokens=6,
                    cost={"USD": Decimal("0.2")},
                ),
            ],
            {
                "prompt_tokens": 32,
                "completion_tokens": 4,
                "total_tokens": 36,
                "prompt_tokens_details": {"cached_tokens": 10},
                "cost_usd": "0.3",
            },
            id="sums-every-selection-exactly",
        ),
        pytest.param(
            [
                entry(tokens_in=10, tokens_out=1, cache_read_tokens=4, cost={"USD": Decimal("0.0000001")}),
                entry(tokens_in=10, tokens_out=1, cost={"USD": Decimal("12345678901234567890.0000002")}),
            ],
            {
                "prompt_tokens": 20,
                "completion_tokens": 2,
                "total_tokens": 22,
                "cost_usd": "12345678901234567890.0000003",
            },
            id="one-unknown-cache-read-omits-details-decimal-stays-exact",
        ),
        pytest.param(
            [entry(tokens_in=10, tokens_out=1, cost={"USD": Decimal("0.1")}), entry(tokens_in=10, tokens_out=1)],
            {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            id="one-unknown-cost-omits-cost",
        ),
        pytest.param(
            [entry(tokens_in=10, tokens_out=1, cost={"EUR": Decimal("0.1")})],
            {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            id="non-usd-cost-omits-cost",
        ),
        pytest.param([entry(tokens_in=10, tokens_out=1), entry(tokens_in=10)], None, id="unknown-tokens-out"),
        pytest.param([entry(tokens_out=1, cache_read_tokens=0)], None, id="unknown-tokens-in"),
        pytest.param([], None, id="no-entries"),
        pytest.param(None, None, id="no-usage"),
    ],
)
def test_usage_projection(entries, expected):
    usage = None if entries is None else binding.Usage(entries)
    assert project_usage(usage) == expected


@pytest.mark.parametrize("include_usage", [True, False])
def test_include_usage_accepted(include_usage):
    body = request(stream=True) | {"stream_options": {"include_usage": include_usage}}
    assert project_request(body)[1] is True


@pytest.mark.parametrize("include_usage", ["true", 1, None])
def test_include_usage_refused_unless_boolean(include_usage):
    body = request(stream=True) | {"stream_options": {"include_usage": include_usage}}
    with pytest.raises(InvalidRequestError) as caught:
        project_request(body)
    assert caught.value.field == "stream_options.include_usage"


KNOWN_USAGE = binding.Usage(
    [entry(tokens_in=30, tokens_out=4, cache_read_tokens=20, cache_write_tokens=2, cost={"USD": Decimal("0.0421")})]
)
KNOWN_PROJECTION = {
    "prompt_tokens": 32,
    "completion_tokens": 4,
    "total_tokens": 36,
    "prompt_tokens_details": {"cached_tokens": 20},
    "cost_usd": "0.0421",
}


@pytest.mark.parametrize("known", [False, True], ids=["unknown-usage", "known-usage"])
@pytest.mark.parametrize("stream", [False, True])
async def test_projection_fixture_drops_every_non_reply_event(monkeypatch, stream, known):
    from amplifier_agent_http import _app

    usage = KNOWN_USAGE if known else binding.Usage([entry(tokens_in=3)])
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
    app = create_app(Settings("contract-token"), binding.AgentOptions(tools=[]))
    async with app.router.lifespan_context(app), socket_server(app, lifespan="off") as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": "Bearer contract-token"},
        ) as client:
            response = await client.post("/v1/chat/completions", json=request(stream=stream))
        assert closed == ["session"]
        assert response.status_code == 200
        if stream:
            received = frames(response)
            check_projection(
                received,
                {
                    "deltas": [[{"type": "text", "text": "Reply"}]],
                    "terminal": {"content": [{"type": "text", "text": "Reply"}]},
                },
            )
            assert received[-1] == "[DONE]"
            assert received[-2]["choices"][0]["finish_reason"] == "stop"
            assert received[-2].get("usage") == (KNOWN_PROJECTION if known else None)
            assert all("usage" not in frame for frame in received[:-2])
        else:
            http_shapes.completion(response.json())
            assert response.json()["choices"][0]["message"]["content"] == "Reply"
            assert response.json().get("usage") == (KNOWN_PROJECTION if known else None)
        for excluded in ("Private reasoning", "Tool output", "read_file", "request-id", "session-id", "tokens_in"):
            assert excluded not in response.text
    assert closed == ["session", "agent"]


def image_request(part, role="user"):
    return {"model": "amplifier", "messages": [{"role": role, "content": [part]}]}


def test_data_url_image_projects_to_an_image_part():
    part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo=", "detail": "low"}}
    _, _, turn_input = project_request(image_request(part))
    assert turn_input.history is not None
    assert turn_input.history[0].content == [binding.ImagePart(media_type="image/png", data="iVBORw0KGgo=")]


@pytest.mark.parametrize(
    ("part", "role", "field"),
    [
        ({"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}, "user", "image_url.url"),
        ({"type": "image_url", "image_url": {"url": "data:image/png,raw"}}, "user", "image_url.url"),
        ({"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": "max"}}, "user", "detail"),
        ({"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}, "system", "content[0]"),
        ({"type": "input_audio", "input_audio": {"data": "AA==", "format": "wav"}}, "user", "content[0]"),
    ],
    ids=["remote", "not-base64", "detail", "role", "audio"],
)
def test_image_projection_refusals_name_the_field(part, role, field):
    with pytest.raises(InvalidRequestError) as caught:
        project_request(image_request(part, role))
    assert caught.value.field.startswith("messages[0].content[0]")
    assert caught.value.field.endswith(field)
    assert caught.value.remedy


def test_agent_input_fields_map_to_request_params():
    body = {"messages": [{"role": "user", "content": "Text"}, {"role": "user", "content": [{}, {}]}]}
    assert request_param("input.history[0].content[0]", body) == "messages[0].content"
    assert request_param("input.history[1].content[1].media_type", body) == "messages[1].content[1].image_url.url"
    assert request_param("input.history[1].content[0]", body) == "messages[1].content[0]"
    assert request_param("input.history[1].role", body) == "messages[1].role"
    assert request_param("input.content", body) is None
    assert request_param(None, body) is None
