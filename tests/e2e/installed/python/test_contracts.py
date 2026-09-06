"""Observe installed Python request policy and reasoning continuity."""

import json

import pytest

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import MODELS, provider_service
from conformance.fixtures.reasoning import reasoning_service
from tests.e2e.installed.python.driver import probe


@pytest.mark.parametrize("store", [False, True])
async def test_installed_streaming_request_overrides(store, tmp_path):
    requests = []
    async with socket_server(provider_service("openai", requests)) as url:
        result = await probe(
            "openai",
            url,
            tmp_path,
            {"mode": "restart", "rounds": 2},
            {
                "extra_request_params": {
                    "openai": {"org.example.setting": {"nested": [1, "two"]}, "store": store}
                }
            },
        )
    assert result["kind"] == "done" and result["histories"] == [1, 2]
    assert len(requests) == 2
    for request in requests:
        assert request["org.example.setting"] == {"nested": [1, "two"]}
        assert request["store"] is store
        assert "previous_response_id" not in request
        assert "conversation" not in request
    replay = json.dumps(requests[-1])
    assert all(
        text in replay for text in ("Visible question 0", "Wire reply", "Visible question 1")
    )


async def test_installed_unknown_host_key_remedy(tmp_path):
    requests = []
    async with socket_server(provider_service("openai", requests)) as url:
        result = await probe(
            "openai", url, tmp_path, {"mode": "invalid_host"}, {"zzzzzz": True}
        )
    assert result["kind"] == "error"
    assert result["error"]["code"] == "invalid_input"
    assert "zzzzzz" in result["error"]["message"]
    assert result["error"]["remedy"] == "Use workspace."
    assert requests == []

@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("mode", ["restart", "age", "size", "tool"])
async def test_installed_native_reasoning_replay(provider, mode, tmp_path):
    requests = []
    application, signatures, validate = reasoning_service(
        provider, requests, mode, tool="observe" if mode == "tool" else None
    )
    rounds = 8 if mode == "age" else 2
    async with socket_server(application) as url:
        result = await probe(
            provider,
            url,
            tmp_path,
            {"mode": mode, "rounds": rounds, "tool": mode == "tool"},
        )
    assert result["histories"] == list(range(1, rounds + 1))
    assert len(requests) == rounds + (mode == "tool")
    replay = json.dumps(requests[-1])
    for index in range(rounds):
        assert f"Visible question {index}" in replay
    if mode == "restart":
        assert signatures[0] in replay
    elif mode == "tool":
        assert len(result["effects"]) == 1 and validate(requests[1]) is None
        assert signatures[0] in json.dumps(requests[1])
        assert (
            validate(
                json.loads(json.dumps(requests[1]).replace(signatures[0], "Truncated signature"))
            )
            is not None
        )
        assert "Confirmed effect" in replay
        if provider == "gemini":
            parts = [part for message in requests[-1]["contents"] for part in message["parts"]]
            assert not any(
                part.get("thought") and part.get("thoughtSignature") == signatures[0]
                for part in parts
            )
            assert any(
                "functionCall" in part and part.get("thoughtSignature") == signatures[0]
                for part in parts
            )
        else:
            assert signatures[0] not in replay
    else:
        assert signatures[0] not in replay
        if mode == "age":
            assert signatures[-2] in replay
