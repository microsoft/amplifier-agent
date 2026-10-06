"""The HTTP face takes the reasoning effort only from server-start settings."""

from contextlib import asynccontextmanager
import json

from amplifier_agent import AgentOptions
from amplifier_agent_http import Settings, create_app
import httpx
import pytest

from tests.support import http_shapes
from tests.support.engine import provision
from tests.support.http_server import socket_server

TOKEN = "contract-token"


@asynccontextmanager
async def face(monkeypatch):
    probe = provision(monkeypatch, [{"text": "Hello"}])
    app = create_app(Settings(token=TOKEN), AgentOptions(provider="anthropic", model="claude-sonnet-5", tools=[]))
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}) as client,
    ):
        yield client, probe


def completion(**fields):
    return {"model": "amplifier", "messages": [{"role": "user", "content": "Hello"}], **fields}


async def test_request_reasoning_effort_is_refused_as_per_request_configuration(monkeypatch):
    async with face(monkeypatch) as (client, probe):
        response = await client.post("/v1/chat/completions", json=completion(reasoning_effort="low"))
    assert response.status_code == 400
    body = response.json()
    http_shapes.error(body)
    assert body["error"]["code"] == "invalid_input"
    assert body["error"]["param"] == "reasoning_effort"
    assert probe.requests == []


async def test_request_reasoning_effort_refusal_names_the_server_setting(monkeypatch):
    async with face(monkeypatch) as (client, _):
        response = await client.post("/v1/chat/completions", json=completion(reasoning_effort="low"))
    assert "AMPLIFIER_AGENT_REASONING_EFFORT" in response.json()["error"]["message"]


@pytest.mark.parametrize("source", ["environment", "file"])
async def test_server_start_reasoning_effort_applies_to_every_request(monkeypatch, isolated_host, source):
    if source == "environment":
        monkeypatch.setenv("AMPLIFIER_AGENT_REASONING_EFFORT", "low")
    else:
        isolated_host.write_text(json.dumps({"reasoning_effort": "low"}))
    async with face(monkeypatch) as (client, probe):
        response = await client.post("/v1/chat/completions", json=completion())
    assert response.status_code == 200, response.text
    assert [request["reasoning_effort"] for request in probe.requests] == ["low"]


async def test_server_without_a_setting_runs_at_the_default(monkeypatch):
    async with face(monkeypatch) as (client, probe):
        response = await client.post("/v1/chat/completions", json=completion())
    assert response.status_code == 200, response.text
    assert [request["reasoning_effort"] for request in probe.requests] == ["medium"]
