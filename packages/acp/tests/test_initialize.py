from importlib.metadata import version

from acp import PROTOCOL_VERSION, RequestError
from acp.schema import ClientCapabilities, Implementation
import pytest


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
            "sessionCapabilities": {"resume": {}, "close": {}, "delete": {}, "additionalDirectories": {}},
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
        pytest.param(lambda connection, session: connection.list_sessions(), id="session/list"),
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
