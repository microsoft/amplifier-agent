"""Exercise installed native artifacts against a real provider socket."""

import asyncio
import os
import socket
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from conformance.fixtures.anthropic_service import anthropic_service
from conformance.fixtures.http_server import socket_server


def artifact(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"Set {name} to exercise the installed artifact.")
    return value


def consumer_environment(url):
    return {
        **os.environ,
        "PATH": "/nonexistent",
        "PYTHONPATH": "",
        "ANTHROPIC_API_KEY": "fixture-api-key",
        "ANTHROPIC_BASE_URL": url,
        "AMPLIFIER_AGENT_PROVIDER": "anthropic",
        "AMPLIFIER_AGENT_MODEL": "claude-sonnet-5",
    }


async def test_installed_typescript_production_runtime():
    executable = artifact("AMPLIFIER_AGENT_NODE_EXECUTABLE")
    project = artifact("AMPLIFIER_AGENT_NODE_PROJECT")
    requests = []
    program = Path(__file__).with_name("production.mjs").read_text()
    async with socket_server(anthropic_service(requests)) as url:
        child = await asyncio.create_subprocess_exec(
            executable,
            "--input-type=module",
            "--eval",
            program,
            cwd=project,
            env=consumer_environment(url),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        assert child.returncode == 0, stderr.decode()
        assert b"Installed production runtime passed." in stdout
    assert len(requests) == 1
    assert requests[0]["model"] == "claude-sonnet-5"


async def test_installed_standalone_http_face(tmp_path):
    executable = artifact("AMPLIFIER_AGENT_FACE_EXECUTABLE")
    requests = []
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    base = f"http://127.0.0.1:{port}/v1"
    async with socket_server(anthropic_service(requests)) as url:
        with (tmp_path / "face.log").open("wb") as log:
            child = await asyncio.create_subprocess_exec(
                executable,
                cwd=tmp_path,
                env={
                    **consumer_environment(url),
                    "AMPLIFIER_AGENT_FACE_TOKEN": "consumer-token",
                    "AMPLIFIER_AGENT_FACE_PORT": str(port),
                },
                stdout=log,
                stderr=log,
            )
            try:
                async with httpx.AsyncClient() as probe:
                    async with asyncio.timeout(20):
                        while True:
                            assert child.returncode is None, (tmp_path / "face.log").read_text()
                            try:
                                response = await probe.get(base + "/models")
                                if response.status_code == 401:
                                    break
                            except httpx.ConnectError:
                                pass
                            await asyncio.sleep(0.05)
                async with AsyncOpenAI(base_url=base, api_key="consumer-token") as client:
                    models = await client.models.list()
                    assert models.data[0].id == "amplifier"
                    message = [{"role": "user", "content": "Hello"}]
                    reply = await client.chat.completions.create(
                        model="amplifier", messages=message
                    )
                    assert reply.choices[0].message.content == "Wire reply"
                    stream = await client.chat.completions.create(
                        model="amplifier", messages=message, stream=True
                    )
                    chunks = [chunk async for chunk in stream]
                    assert "".join(c.choices[0].delta.content or "" for c in chunks) == "Wire reply"
                    assert chunks[-1].choices[0].finish_reason == "stop"
            finally:
                if child.returncode is None:
                    child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 10)
                except TimeoutError:
                    child.kill()
                    await child.wait()
    assert len(requests) == 2
    assert all(request["model"] == "claude-sonnet-5" for request in requests)
