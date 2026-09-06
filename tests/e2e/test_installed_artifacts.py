"""Exercise installed public surfaces and restart from local transcripts alone."""

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
from openai import APIStatusError, AsyncOpenAI

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import KEY_ENV, MODELS, URL_ENV, provider_service

ROOT = Path(__file__).parents[2]


def artifact(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"Installed acceptance requires {name}.", pytrace=False)
    return value


@pytest.fixture(autouse=True)
def installed_requirements(request):
    parameters = request.node.callspec.params
    surfaces = (
        [parameters["surface"]] if "surface" in parameters else list(parameters.get("bindings", []))
    )
    required = set()
    for surface in surfaces:
        prefix = "PYTHON" if surface == "python" else "NODE"
        required.update(
            {f"AMPLIFIER_AGENT_{prefix}_EXECUTABLE", f"AMPLIFIER_AGENT_{prefix}_PROJECT"}
        )
    if not surfaces:
        required.add("AMPLIFIER_AGENT_FACE_EXECUTABLE")
        if "authority" in parameters:
            required.add("AMPLIFIER_AGENT_PYTHON_EXECUTABLE")
    for name in sorted(required):
        artifact(name)


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


async def caller(surface, provider, url, directory, mode):
    if surface == "python":
        executable = artifact("AMPLIFIER_AGENT_PYTHON_EXECUTABLE")
        project = artifact("AMPLIFIER_AGENT_PYTHON_PROJECT")
        arguments = ["-c", Path(__file__).with_name("production.py").read_text()]
    else:
        executable = artifact("AMPLIFIER_AGENT_NODE_EXECUTABLE")
        project = artifact("AMPLIFIER_AGENT_NODE_PROJECT")
        arguments = [
            "--input-type=module",
            "--eval",
            Path(__file__).with_name("production.mjs").read_text(),
        ]
    environment = {
        **consumer_environment(provider, url, directory),
        "E2E_MODE": mode,
        "E2E_SESSION_ID": "installed-session",
        "E2E_EFFECT_LEDGER": str(directory / "effects.jsonl"),
        "E2E_EXPECTED_HISTORY": str(directory / "expected-history.json"),
    }
    log = directory / f"{surface}-{mode}.log"
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


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("surface", ["python", "typescript"])
async def test_installed_production_runtime(provider, surface, tmp_path):
    requests = []
    release = asyncio.Event()
    async with socket_server(provider_service(provider, requests, release=release)) as url:
        child, log = await caller(surface, provider, url, tmp_path, "ephemeral")
        try:
            first = await read_kind(child, "output", log)
            assert first["content"] == [{"type": "text", "text": "Wire "}]
            assert child.returncode is None
            release.set()
            result = await read_kind(child, "result", log)
            assert await asyncio.wait_for(child.wait(), 15) == 0, log.read_text()
            assert result["result"]["state"] == "success"
            assert result["history"][0]["result"] == result["result"]
        finally:
            release.set()
            await stop(child)
    assert len(requests) == 1
    assert_stateless(provider, requests)


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("surface", ["python", "typescript"])
@pytest.mark.parametrize(
    "mode", ["ephemeral_failure", "ephemeral_partial_failure", "ephemeral_cancel"]
)
async def test_installed_provider_failure_and_cancellation(provider, surface, mode, tmp_path):
    requests = []
    release = asyncio.Event()
    application = provider_service(
        provider,
        requests,
        release=release if mode == "ephemeral_cancel" else None,
        failure=503 if mode == "ephemeral_failure" else None,
        partial_failure=mode == "ephemeral_partial_failure",
    )
    async with socket_server(application) as url:
        child, log = await caller(surface, provider, url, tmp_path, mode)
        try:
            result = await read_kind(child, "result", log)
            assert await asyncio.wait_for(child.wait(), 15) == 0, log.read_text()
            terminal = result["result"]
            assert terminal["state"] == ("cancelled" if mode == "ephemeral_cancel" else "failure")
            assert terminal["error"]["remedy"]
            assert result["history"][0]["result"] == terminal
        finally:
            release.set()
            await stop(child)
    assert len(requests) == 1
    assert_stateless(provider, requests)


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize(
    "bindings",
    [
        ("python", "python"),
        ("typescript", "typescript"),
        ("python", "typescript"),
        ("typescript", "python"),
    ],
    ids=lambda pair: "-to-".join(pair),
)
async def test_installed_durable_restart_discards_every_process(provider, bindings, tmp_path):
    initial_ledger = tmp_path / "initial-provider.jsonl"
    resumed_ledger = tmp_path / "resumed-provider.jsonl"
    async with provider_process(provider, initial_ledger) as (url, first_provider_pid):
        first, log = await caller(bindings[0], provider, url, tmp_path, "create")
        try:
            completed = await read_kind(first, "result", log)
            assert completed["result"]["state"] == "success"
            assert len(completed["history"]) == 1
            assert first.returncode is None
            (tmp_path / "expected-history.json").write_text(json.dumps(completed["history"]))
            effects = requests_at(tmp_path / "effects.jsonl")
            assert len(effects) == 1
            assert effects[0]["pid"] == first.pid
        finally:
            await stop(first)
    async with provider_process(provider, resumed_ledger) as (url, second_provider_pid):
        assert second_provider_pid != first_provider_pid
        second, log = await caller(bindings[1], provider, url, tmp_path, "resume")
        try:
            resumed = await read_kind(second, "result", log)
            assert await asyncio.wait_for(second.wait(), 15) == 0, log.read_text()
            assert resumed["before"] == completed["history"]
            assert resumed["history"][0] == completed["history"][0]
            assert len(resumed["history"]) == 2
            assert resumed["result"]["usage"]["entries"][0]["tokens_in"] == "20"
        finally:
            await stop(second)
    assert requests_at(tmp_path / "effects.jsonl") == effects
    initial, continuation = requests_at(initial_ledger), requests_at(resumed_ledger)
    assert len(initial) == 2
    assert len(continuation) == 1
    assert_stateless(provider, initial + continuation)
    replay = json.dumps(continuation[0])
    assert replay.count("effect-recorded") == 1
    assert "Hello" in replay
    assert "Resume this conversation" in replay
    assert "Wire reply" in replay
    signature = {
        "anthropic": "fixture-signature",
        "openai": "opaque-fixture-reasoning",
        "gemini": "c2lnbmF0dXJl",
    }[provider]
    assert signature in replay


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
