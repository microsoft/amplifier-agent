from importlib.metadata import version
import json

from acp import PROTOCOL_VERSION, RequestError
from acp.schema import ClientCapabilities, Implementation
import pytest

from tests.support.credentials import DEFAULT_CREDENTIALS


async def test_initialize_answers_the_contracted_capabilities_and_auth_method(connect):
    async with connect() as (connection, recorder):
        await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="test-client", version="1"),
        )
    result = next(message["result"] for message in recorder.wire if "result" in message)
    assert result == {
        "protocolVersion": 1,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
            "mcpCapabilities": {"http": True, "sse": False},
            "sessionCapabilities": {"list": {}, "resume": {}, "close": {}, "delete": {}, "additionalDirectories": {}},
        },
        "authMethods": [
            {
                "id": "provider-credentials",
                "name": "Provider credentials",
                "description": "Set the selected provider's credential variables in the agent's environment.",
            }
        ],
        "agentInfo": {"name": "amplifier-agent", "version": version("amplifier-agent-acp")},
    }


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda connection, session: connection.set_session_mode(session_id=session, mode_id="code"),
            id="session/set_mode",
        ),
        pytest.param(
            lambda connection, session: connection.fork_session(session_id=session, cwd="/"), id="session/fork"
        ),
        pytest.param(lambda connection, session: connection.list_providers(), id="providers/list"),
        pytest.param(lambda connection, session: connection.ext_method("amplifier/status", {}), id="extension"),
    ],
)
async def test_every_method_not_offered_is_not_found(connect, tmp_path, call):
    async with connect() as (connection, _):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        session = await connection.new_session(cwd=str(tmp_path))
        with pytest.raises(RequestError) as raised:
            await call(connection, session.session_id)
    assert raised.value.code == -32601


@pytest.mark.parametrize(
    ("host", "variable"),
    [
        pytest.param({"provider": "anthropic"}, "ANTHROPIC_API_KEY", id="selected provider"),
        pytest.param({}, "OPENAI_API_KEY", id="no provider"),
    ],
)
async def test_new_session_without_credentials_requires_auth(
    connect, isolated_host, monkeypatch, tmp_path, host, variable
):
    for key in DEFAULT_CREDENTIALS:
        monkeypatch.delenv(key, raising=False)
    isolated_host.write_text(json.dumps(host))
    async with connect() as (connection, _):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        with pytest.raises(RequestError) as raised:
            await connection.new_session(cwd=str(tmp_path))
    assert raised.value.code == -32000
    assert variable in str(raised.value)


@pytest.mark.parametrize("policy", ["allow", "deny"])
async def test_a_host_policy_offers_no_approvals_mode(connect, isolated_host, tmp_path, policy):
    isolated_host.write_text(json.dumps({"approvals": policy}))
    async with connect() as (connection, _):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        session = await connection.new_session(cwd=str(tmp_path))
        with pytest.raises(RequestError) as raised:
            await connection.set_config_option(config_id="approvals", session_id=session.session_id, value="ask")
    assert "approvals" not in [option.id for option in session.config_options]
    assert raised.value.code == -32602


async def test_an_unknown_approvals_mode_is_refused(connect, tmp_path):
    async with connect() as (connection, _):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        session = await connection.new_session(cwd=str(tmp_path))
        with pytest.raises(RequestError) as raised:
            await connection.set_config_option(config_id="approvals", session_id=session.session_id, value="always")
    assert raised.value.code == -32602
    assert "allow-reads" in str(raised.value)
