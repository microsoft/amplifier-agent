"""Launch isolated installed consumers and controlled provider services."""

import asyncio
import json
import os
import signal
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from conformance.fixtures.provider_services import KEY_ENV, MODELS, URL_ENV

ROOT = Path(__file__).parents[3]


def artifact(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"Installed acceptance requires {name}.", pytrace=False)
    return value

def consumer_environment(provider, url, directory):
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "agent-config.json"
    config.write_text("{}")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("AMPLIFIER_", "ANTHROPIC_", "OPENAI_", "GOOGLE_", "GEMINI_", "AZURE_")
        )
    }
    return {
        **environment,
        "PATH": "/nonexistent",
        "PYTHONPATH": "",
        KEY_ENV[provider]: "fixture-api-key",
        URL_ENV[provider]: url,
        "AMPLIFIER_AGENT_CONFIG": str(config),
        "AMPLIFIER_AGENT_PROVIDER": provider,
        "AMPLIFIER_AGENT_MODEL": MODELS[provider],
        "AMPLIFIER_AGENT_STORAGE": str(directory / "transcripts"),
    }

async def stop(child):
    if child.returncode is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.wait_for(child.wait(), 10)

async def read_kind(child, kind, log):
    assert child.stdout is not None
    try:
        async with asyncio.timeout(30):
            while line := await child.stdout.readline():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if value.get("kind") == kind:
                    return value
    except TimeoutError:
        pytest.fail(f"Timed out waiting for installed caller {kind}: {log.read_text()}")
    pytest.fail(f"The installed caller stopped before {kind}: {log.read_text()}")

@asynccontextmanager
async def provider_process(provider, ledger):
    log = ledger.with_suffix(".log")
    with log.open("wb") as output:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "conformance.fixtures.provider_service_process",
            provider,
            str(ledger),
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=output,
            start_new_session=True,
        )
    try:
        ready = await read_kind(child, "provider", log)
        yield ready["url"], child.pid
    finally:
        await stop(child)

def requests_at(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def assert_stateless(provider, requests):
    for request in requests:
        assert "previous_response_id" not in request
        if provider == "openai":
            assert request["store"] is False
        if provider != "gemini":
            assert request["model"] == MODELS[provider]

@asynccontextmanager
async def face(provider, url, directory, authority=None):
    command = [artifact("AMPLIFIER_AGENT_FACE_EXECUTABLE")]
    if authority is not None:
        command = [
            artifact("AMPLIFIER_AGENT_PYTHON_EXECUTABLE"),
            "-c",
            "\n".join(
                [
                    "import uvicorn",
                    "from amplifier_agent import AgentOptions",
                    "from amplifier_agent_http import Settings, create_app",
                    "settings = Settings.from_environment()",
                    f"app = create_app(settings, AgentOptions(approvals={authority!r}))",
                    "uvicorn.run(app, host=settings.bind, port=settings.port, log_level='error')",
                ]
            ),
        ]
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    base = f"http://127.0.0.1:{port}/v1"
    log = directory / "face.log"
    with log.open("wb") as output:
        child = await asyncio.create_subprocess_exec(
            *command,
            cwd=directory,
            env={
                **consumer_environment(provider, url, directory),
                "AMPLIFIER_AGENT_FACE_TOKEN": "consumer-token",
                "AMPLIFIER_AGENT_FACE_PORT": str(port),
            },
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    try:
        async with httpx.AsyncClient() as probe:
            async with asyncio.timeout(20):
                while True:
                    assert child.returncode is None, log.read_text()
                    try:
                        if (await probe.get(base + "/models")).status_code == 401:
                            break
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(0.05)
        yield base
    finally:
        if child.returncode is None:
            child.terminate()
        try:
            await asyncio.wait_for(child.wait(), 10)
        except TimeoutError:
            await stop(child)

async def start_consumer(executable, arguments, project, environment, log):
    with log.open("wb") as output:
        child = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            cwd=project,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=output,
            start_new_session=True,
        )
    return child, log
