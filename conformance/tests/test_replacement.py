from types import SimpleNamespace

import pytest

from conformance.drivers.python import run
from conformance.fixtures.replacement import create_engine
from conformance.fixtures.scripted_provider import SCENARIOS
from conformance.observations import verify


@pytest.mark.parametrize(
    "case",
    [case for case in SCENARIOS if case["id"] in {"text", "history", "provider_failure"}],
    ids=lambda case: case["id"],
)
async def test_same_public_cases_with_independent_engine(monkeypatch, case):
    from amplifier_agent_engine._engine import assembly

    monkeypatch.setattr(assembly, "create_engine", create_engine)
    verify(case, await run(case, SimpleNamespace(active=0)))


async def test_http_with_independent_engine(monkeypatch):
    import httpx
    from amplifier_agent_engine._engine import assembly
    from amplifier_agent_http import Settings, create_app

    monkeypatch.setattr(assembly, "create_engine", create_engine)
    app = create_app(Settings("test-token"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://face",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "amplifier", "messages": [{"role": "user", "content": "Say hello"}]},
            )
            assert response.status_code == 200
            assert response.json()["choices"][0]["message"]["content"] == "Hello world"
