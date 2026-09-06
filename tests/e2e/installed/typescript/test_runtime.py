"""Exercise the installed TypeScript binding and durable native runtime."""

import asyncio
import json

import pytest

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import MODELS, provider_service
from tests.e2e.installed.support import (
    assert_stateless,
    provider_process,
    read_kind,
    requests_at,
    stop,
)
from tests.e2e.installed.typescript.driver import caller


@pytest.mark.parametrize("provider", MODELS)
async def test_installed_production_runtime(provider, tmp_path):
    requests = []
    release = asyncio.Event()
    async with socket_server(provider_service(provider, requests, release=release)) as url:
        child, log = await caller(provider, url, tmp_path, "ephemeral")
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
@pytest.mark.parametrize(
    "mode", ["ephemeral_failure", "ephemeral_partial_failure", "ephemeral_cancel"]
)
async def test_installed_provider_failure_and_cancellation(provider, mode, tmp_path):
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
        child, log = await caller(provider, url, tmp_path, mode)
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
async def test_installed_durable_restart_discards_every_process(provider, tmp_path):
    initial_ledger = tmp_path / "initial-provider.jsonl"
    resumed_ledger = tmp_path / "resumed-provider.jsonl"
    async with provider_process(provider, initial_ledger) as (url, first_provider_pid):
        first, log = await caller(provider, url, tmp_path, "create")
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
        second, log = await caller(provider, url, tmp_path, "resume")
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
