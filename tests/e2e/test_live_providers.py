"""Explicit live acceptance for installed Python, TypeScript, and HTTP consumers."""

import asyncio
import json
import os
import socket
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from conformance.fixtures.provider_services import KEY_ENV, URL_ENV
from tests.e2e.test_installed_artifacts import artifact, consumer_environment, read_kind, stop

LIVE_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-3.7-flash",
}


def live_environment(provider, directory):
    environment = consumer_environment(provider, "", directory)
    environment.pop(URL_ENV[provider])
    credential = os.environ.get(KEY_ENV[provider])
    if provider == "gemini":
        credential = credential or os.environ.get("GEMINI_API_KEY")
    if not credential:
        pytest.fail(f"Live acceptance requires a credential for {provider}.", pytrace=False)
    environment[KEY_ENV[provider]] = credential
    if os.environ.get(URL_ENV[provider]):
        environment[URL_ENV[provider]] = os.environ[URL_ENV[provider]]
    model = os.environ.get(f"E2E_LIVE_{provider.upper()}_MODEL", LIVE_MODELS[provider])
    if provider == "anthropic" and model not in {"claude-sonnet-5", "claude-opus-5"}:
        pytest.fail("Live Anthropic acceptance requires claude-sonnet-5 or claude-opus-5.")
    environment["AMPLIFIER_AGENT_MODEL"] = model
    return environment


async def live_caller(surface, provider, directory, mode):
    if surface == "python":
        executable = artifact("AMPLIFIER_AGENT_PYTHON_EXECUTABLE")
        project = artifact("AMPLIFIER_AGENT_PYTHON_PROJECT")
        arguments = ["-c", Path(__file__).with_name("live.py").read_text()]
    else:
        executable = artifact("AMPLIFIER_AGENT_NODE_EXECUTABLE")
        project = artifact("AMPLIFIER_AGENT_NODE_PROJECT")
        arguments = [
            "--input-type=module",
            "--eval",
            Path(__file__).with_name("live.mjs").read_text(),
        ]
    environment = {
        **live_environment(provider, directory),
        "E2E_MODE": mode,
        "E2E_EXPECTED_HISTORY": str(directory / "history.json"),
    }
    log = directory / f"live-{surface}-{mode}.log"
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


@pytest.mark.parametrize("provider", LIVE_MODELS)
@pytest.mark.parametrize("surface", ["python", "typescript"])
async def test_live_installed_binding_stream_tool_and_resume(provider, surface, tmp_path):
    reports = []
    async with asyncio.timeout(115):
        for mode in ("create", "resume"):
            child, log = await live_caller(surface, provider, tmp_path, mode)
            try:
                report = await read_kind(child, "result", log)
                assert await asyncio.wait_for(child.wait(), 15) == 0, log.read_text()
                assert report["state"] == "success" and report["streamed"]
                assert report["effects"] == (1 if mode == "create" else 0)
                assert report["history_count"] == (1 if mode == "create" else 2)
                reports.append(report)
            finally:
                await stop(child)
    assert reports[0]["pid"] != reports[1]["pid"]


@pytest.mark.parametrize("provider", LIVE_MODELS)
async def test_live_installed_http_stream_and_builtin(provider, tmp_path):
    executable = artifact("AMPLIFIER_AGENT_PYTHON_EXECUTABLE")
    environment = live_environment(provider, tmp_path)
    token = "live-filesystem-proof-71a90b"
    probe_file = tmp_path / "probe.txt"
    probe_file.write_text(token)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    environment.update(
        AMPLIFIER_AGENT_FACE_TOKEN="live-consumer-token", AMPLIFIER_AGENT_FACE_PORT=str(port)
    )
    program = "\n".join(
        [
            "import uvicorn",
            "from amplifier_agent import AgentOptions",
            "from amplifier_agent_http import Settings, create_app",
            "settings = Settings.from_environment()",
            "app = create_app(settings, AgentOptions(approvals='allow', "
            "instructions='Use only read_file when asked for a tool. Keep responses short.'))",
            "uvicorn.run(app, host=settings.bind, port=settings.port, log_level='error')",
        ]
    )
    log = tmp_path / "live-http.log"
    with log.open("wb") as output:
        child = await asyncio.create_subprocess_exec(
            executable,
            "-c",
            program,
            cwd=tmp_path,
            env=environment,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    base = f"http://127.0.0.1:{port}/v1"
    try:
        async with asyncio.timeout(115):
            async with httpx.AsyncClient() as probe:
                async with asyncio.timeout(25):
                    while True:
                        assert child.returncode is None, log.read_text()
                        try:
                            if (await probe.get(base + "/models")).status_code == 401:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.05)
            async with AsyncOpenAI(
                base_url=base, api_key="live-consumer-token", max_retries=0
            ) as client:
                stream = await client.chat.completions.create(
                    model="amplifier",
                    stream=True,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Call read_file on {json.dumps(str(probe_file))}, then reply with its exact contents.",
                        }
                    ],
                )
                chunks = [chunk async for chunk in stream]
                assert chunks[-1].choices[0].finish_reason == "stop"
                assert token in "".join(chunk.choices[0].delta.content or "" for chunk in chunks)
                assert probe_file.read_text() == token
    finally:
        await stop(child)
