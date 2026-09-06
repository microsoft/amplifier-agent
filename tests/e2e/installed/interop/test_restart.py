"""Resume installed durable sessions through the other native binding."""

import asyncio
import json

import pytest

from conformance.fixtures.provider_services import MODELS
from tests.e2e.installed.python.driver import caller as python_caller
from tests.e2e.installed.support import (
    assert_stateless,
    provider_process,
    read_kind,
    requests_at,
    stop,
)
from tests.e2e.installed.typescript.driver import caller as typescript_caller

CALLERS = {"python": python_caller, "typescript": typescript_caller}


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize(
    "bindings",
    [
        ("python", "typescript"),
        ("typescript", "python"),
    ],
    ids=lambda pair: "-to-".join(pair),
)
async def test_installed_durable_restart_discards_every_process(provider, bindings, tmp_path):
    initial_ledger = tmp_path / "initial-provider.jsonl"
    resumed_ledger = tmp_path / "resumed-provider.jsonl"
    async with provider_process(provider, initial_ledger) as (url, first_provider_pid):
        first, log = await CALLERS[bindings[0]](provider, url, tmp_path, "create")
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
        second, log = await CALLERS[bindings[1]](provider, url, tmp_path, "resume")
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
