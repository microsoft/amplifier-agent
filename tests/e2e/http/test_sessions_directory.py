from amplifier_agent import AgentOptions
from amplifier_agent_http import Settings, create_app
import httpx

from tests.support.engine import provision
from tests.support.http_server import socket_server


async def test_the_face_keeps_engine_state_in_the_configured_sessions_directory(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    sessions = tmp_path / "face-sessions"
    monkeypatch.setenv("AMPLIFIER_AGENT_SESSIONS_DIRECTORY", str(sessions))
    provision(monkeypatch, [{"chunks": ["Hello"], "text": "Hello"}])
    app = create_app(
        Settings(token="fixture-token"), AgentOptions(provider="anthropic", model="claude-sonnet-5", tools=[])
    )
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer fixture-token"}) as client,
    ):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "amplifier", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert response.status_code == 200
    captures = list(sessions.glob("sessions/*/context-intelligence/events.jsonl"))
    assert len(captures) == 1
    assert not list(sessions.glob("sessions/*/transcript.jsonl"))
    assert list(home.iterdir()) == []
    assert list(project.iterdir()) == []
