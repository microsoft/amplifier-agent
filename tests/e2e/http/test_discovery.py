"""Provider discovery endpoints under the face's auth and error shape (http-face.v1 section 11)."""

from contextlib import asynccontextmanager

from amplifier_agent import AgentOptions
from amplifier_agent_http import Settings, create_app
from amplifier_core.models import ModelInfo
import httpx
import pytest

from tests.support import http_shapes
from tests.support.engine import provision
from tests.support.http_server import socket_server

TOKEN = "discovery-token"
SECRET = "sentinel-credential-value"
PROVIDER_FIELDS = {"provider", "display_name", "installed", "credentials", "credential_variables"}


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # The real GitHub CLI on a developer machine may hold a login.
    from amplifier_agent_engine._engine import discovery

    monkeypatch.setattr(discovery, "_gh_executable", lambda environment: None)


@asynccontextmanager
async def face(monkeypatch):
    probe = provision(monkeypatch)
    app = create_app(Settings(token=TOKEN), AgentOptions(provider="anthropic", model="claude-sonnet-5", tools=[]))
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}) as client,
    ):
        yield client, probe


def anthropic_models(monkeypatch, models):
    from amplifier_module_provider_anthropic import AnthropicProvider

    async def listed(self, *args, **kwargs):
        return list(models)

    monkeypatch.setattr(AnthropicProvider, "list_models", listed)


async def test_providers_endpoint_lists_provider_records(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    async with face(monkeypatch) as (client, _):
        response = await client.get("/v1/providers")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"object", "data"}
    assert body["object"] == "list"
    assert all(set(record) == PROVIDER_FIELDS for record in body["data"])
    by_name = {record["provider"]: record for record in body["data"]}
    assert by_name["anthropic"]["credentials"] == "found"
    assert "ANTHROPIC_API_KEY" in by_name["anthropic"]["credential_variables"]
    assert SECRET not in response.text


async def test_models_endpoint_lists_model_records(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    anthropic_models(
        monkeypatch,
        [
            ModelInfo(id="claude-a", display_name="Claude A", context_window=200_000, max_output_tokens=64_000),
            ModelInfo(id="claude-b", display_name="Claude B", context_window=0, max_output_tokens=0),
        ],
    )
    async with face(monkeypatch) as (client, _):
        response = await client.get("/v1/providers/anthropic/models")
    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {"id": "claude-a", "display_name": "Claude A", "context_window": 200_000, "max_output_tokens": 64_000},
            {"id": "claude-b", "display_name": "Claude B"},
        ],
    }


@pytest.mark.parametrize("path", ["/v1/providers", "/v1/providers/anthropic/models"])
@pytest.mark.parametrize("token", [None, "wrong"])
async def test_discovery_requires_the_bearer_token(monkeypatch, path, token):
    async with face(monkeypatch) as (client, probe):
        client.headers.clear()
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        response = await client.get(path, headers=headers)
    assert response.status_code == 401
    http_shapes.error(response.json())
    assert "Supply" in response.json()["error"]["message"]
    assert probe.requests == []


@pytest.mark.parametrize(
    ("provider", "status", "code"),
    [("not-a-provider", 400, "invalid_input"), ("anthropic", 502, "provider_failed")],
)
async def test_discovery_failures_ride_the_error_shape(monkeypatch, provider, status, code):
    async with face(monkeypatch) as (client, _):
        response = await client.get(f"/v1/providers/{provider}/models")
    assert response.status_code == status
    error = http_shapes.error(response.json())
    assert error.code == code


async def test_models_endpoint_keeps_its_meaning(monkeypatch):
    async with face(monkeypatch) as (client, _):
        response = await client.get("/v1/models")
    assert response.status_code == 200
    assert [model.id for model in http_shapes.models(response.json())] == ["amplifier"]


async def test_discovery_uses_the_environment_the_server_agents_get(monkeypatch):
    provision(monkeypatch)
    options = AgentOptions(
        provider="anthropic", model="claude-sonnet-5", tools=[], environment={"ANTHROPIC_API_KEY": SECRET}
    )
    app = create_app(Settings(token=TOKEN), options)
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}) as client,
    ):
        response = await client.get("/v1/providers")
    by_name = {record["provider"]: record for record in response.json()["data"]}
    assert by_name["anthropic"]["credentials"] == "found"
    assert SECRET not in response.text
