"""Observe required native reasoning and isolation through installed HTTP."""

import json

import pytest
from openai import AsyncOpenAI

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import MODELS
from conformance.fixtures.reasoning import reasoning_service
from tests.e2e.installed.support import face


@pytest.mark.parametrize("provider", MODELS)
async def test_installed_http_reasoning_has_no_cross_request_state(provider, tmp_path):
    requests = []
    application, signatures, _ = reasoning_service(provider, requests, "size")
    async with socket_server(application) as url:
        async with face(provider, url, tmp_path) as base:
            async with AsyncOpenAI(base_url=base, api_key="consumer-token") as client:
                messages = [{"role": "user", "content": "Visible question 0"}]
                first = await client.chat.completions.create(model="amplifier", messages=messages)
                assert first.choices[0].message.content == "Wire reply"
                messages += [
                    {"role": "assistant", "content": "Wire reply"},
                    {"role": "user", "content": "Visible question 1"},
                ]
                second = await client.chat.completions.create(model="amplifier", messages=messages)
                assert second.choices[0].message.content == "Wire reply"
    assert len(requests) == 2
    replay = json.dumps(requests[-1])
    assert signatures[0] not in replay
    assert all(
        text in replay for text in ("Visible question 0", "Wire reply", "Visible question 1")
    )

@pytest.mark.parametrize("provider", MODELS)
async def test_installed_http_required_tool_reasoning(provider, tmp_path):
    path = tmp_path / "observed.txt"
    path.write_text("Independent file observation")
    requests = []
    application, signatures, validate = reasoning_service(
        provider, requests, "tool", tool="read_file", tool_arguments={"file_path": str(path)}
    )
    async with socket_server(application) as url:
        async with face(provider, url, tmp_path, authority="allow") as base:
            async with AsyncOpenAI(base_url=base, api_key="consumer-token") as client:
                result = await client.chat.completions.create(
                    model="amplifier",
                    messages=[{"role": "user", "content": "Read the observed file"}],
                )
                assert result.choices[0].message.content == "Wire reply"
    assert len(requests) == 2
    assert validate(requests[-1]) is None
    replay = json.dumps(requests[-1])
    assert signatures[0] in replay
    assert "Independent file observation" in replay
    assert validate(json.loads(replay.replace(signatures[0], "Truncated signature"))) is not None
