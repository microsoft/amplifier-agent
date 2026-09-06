"""Exercise installed HTTP authentication, streaming, effects, and isolation."""

import asyncio
import json

import pytest
from openai import APIStatusError, AsyncOpenAI

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import MODELS, provider_service
from tests.e2e.installed.support import assert_stateless, face


@pytest.mark.parametrize("provider", MODELS)
async def test_installed_standalone_http_face(provider, tmp_path):
    requests = []
    async with socket_server(provider_service(provider, requests)) as url:
        async with face(provider, url, tmp_path) as base:
            async with AsyncOpenAI(base_url=base, api_key="consumer-token") as client:
                models = await client.models.list()
                assert models.data[0].id == "amplifier"
                message = [{"role": "user", "content": "Hello"}]
                reply = await client.chat.completions.create(model="amplifier", messages=message)
                assert reply.choices[0].message.content == "Wire reply"
                stream = await client.chat.completions.create(
                    model="amplifier", messages=message, stream=True
                )
                chunks = [chunk async for chunk in stream]
                assert "".join(c.choices[0].delta.content or "" for c in chunks) == "Wire reply"
                assert chunks[-1].choices[0].finish_reason == "stop"
    assert len(requests) == 2
    assert_stateless(provider, requests)

@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("authority", ["allow", "deny"])
async def test_installed_http_builtin_policy_and_isolation(provider, authority, tmp_path):
    requests = []
    content = "Installed filesystem proof"
    file = tmp_path / "fixture.txt"
    file.write_text("Original content")
    application = provider_service(
        provider,
        requests,
        tool="write_file",
        tool_arguments={"file_path": str(file), "content": content},
    )
    async with socket_server(application) as url:
        async with face(provider, url, tmp_path, authority) as base:
            async with AsyncOpenAI(
                base_url=base, api_key="consumer-token", max_retries=0
            ) as client:

                async def complete(prompt):
                    if authority == "allow":
                        return await client.chat.completions.create(
                            model="amplifier", messages=[{"role": "user", "content": prompt}]
                        )
                    with pytest.raises(APIStatusError) as error:
                        await client.chat.completions.create(
                            model="amplifier", messages=[{"role": "user", "content": prompt}]
                        )
                    assert error.value.status_code == 403
                    assert error.value.code == "approval_denied"
                    assert error.value.message

                replies = await asyncio.gather(
                    complete("First isolated caller"), complete("Second isolated caller")
                )
                if authority == "allow":
                    assert all(
                        reply.choices[0].message.content == "Wire reply" for reply in replies
                    )
    assert len(requests) == (4 if authority == "allow" else 2)
    for request in requests:
        encoded = json.dumps(request)
        assert not ("First isolated caller" in encoded and "Second isolated caller" in encoded)
    assert file.read_text() == (content if authority == "allow" else "Original content")
    assert_stateless(provider, requests)
