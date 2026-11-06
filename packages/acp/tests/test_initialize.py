import asyncio
from collections.abc import AsyncIterator
import contextlib
from importlib.metadata import version
from typing import cast

from acp import PROTOCOL_VERSION, Agent, Client, RequestError, connect_to_agent, run_agent
from acp.client.connection import ClientSideConnection
from acp.schema import ClientCapabilities, Implementation, InitializeResponse, TextContentBlock
from amplifier_agent_acp._agent import AcpAgent
import pytest


class SilentClient:
    """A client that the face never calls back during these tests."""


@contextlib.asynccontextmanager
async def connected() -> AsyncIterator[ClientSideConnection]:
    """The face and a client over a loopback socket pair."""
    accepted: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = asyncio.Future()
    server = await asyncio.start_server(
        lambda reader, writer: accepted.set_result((reader, writer)), host="127.0.0.1", port=0
    )
    host, port = server.sockets[0].getsockname()[:2]
    client_reader, client_writer = await asyncio.open_connection(host, port)
    agent_reader, agent_writer = await accepted
    face = asyncio.create_task(run_agent(cast(Agent, AcpAgent()), agent_writer, agent_reader))
    connection = connect_to_agent(cast(Client, SilentClient()), client_writer, client_reader)
    try:
        yield connection
    finally:
        await connection.close()
        client_writer.close()
        agent_writer.close()
        face.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await face
        server.close()
        await server.wait_closed()


async def test_initialize_names_the_agent_and_protocol_version_1():
    async with connected() as connection:
        response = await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(name="test-client", version="1"),
        )
    assert response.protocol_version == 1
    assert response.agent_info == Implementation(name="amplifier-agent", version=version("amplifier-agent-acp"))
    assert response.agent_capabilities == InitializeResponse(protocol_version=1).agent_capabilities
    assert response.auth_methods == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda connection: connection.authenticate(method_id="provider-credentials"), id="authenticate"),
        pytest.param(lambda connection: connection.new_session(cwd="/"), id="session/new"),
        pytest.param(lambda connection: connection.load_session(cwd="/", session_id="s"), id="session/load"),
        pytest.param(
            lambda connection: connection.prompt(prompt=[TextContentBlock(type="text", text="hi")], session_id="s"),
            id="session/prompt",
        ),
    ],
)
async def test_every_other_method_is_not_found(call):
    async with connected() as connection:
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        with pytest.raises(RequestError) as raised:
            await call(connection)
    assert raised.value.code == -32601
