"""The Python binding carries smart_tools across the engine boundary."""

import dataclasses
import json
import os
import socket

import amplifier_agent as sdk
import pytest

from tests.support.engine import provision

SKILL = "amplifier-smart-tools"
HEADING = "# Smart tools"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return path


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Everything here must come from the installed engine; the bundled skill never needs the network.
    resolve = socket.getaddrinfo
    connect = socket.socket.connect

    def local_only(host, *args, **kwargs):
        if host not in LOOPBACK:
            raise OSError(f"network access in a unit test: {host}")
        return resolve(host, *args, **kwargs)

    def loopback_only(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            raise OSError(f"network access in a unit test: {address}")
        return connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)


def options(tmp_path, **fields):
    return sdk.AgentOptions(
        provider="anthropic", model="claude-sonnet-5", approvals="allow", working_directory=str(tmp_path), **fields
    )


async def first_request(monkeypatch, agent_options):
    probe = provision(monkeypatch, [{"text": "Reply"}])
    async with (
        await sdk.create_agent(agent_options) as agent,
        await agent.create_session(sdk.SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(sdk.TurnInput([sdk.TextPart("Reply")]))
    assert result.state == "success", result.error
    return probe.requests[0]


def test_agent_options_declare_an_optional_smart_tools():
    field = {item.name: item for item in dataclasses.fields(sdk.AgentOptions)}["smart_tools"]
    assert field.default is None


@pytest.mark.parametrize("value", [None, True, False])
async def test_the_value_crosses_the_engine_boundary(monkeypatch, tmp_path, value):
    request = await first_request(monkeypatch, options(tmp_path, tools=[], smart_tools=value))
    expected = value is not False
    assert (HEADING in request["messages"][0]["content"]) is expected
    assert (SKILL in json.dumps(request.get("tools") or [])) is expected
