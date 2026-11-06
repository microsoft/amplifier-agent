import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
import os
from typing import Any, cast

from acp import Client, connect_to_agent
from acp.client.connection import ClientSideConnection
from acp.connection import StreamDirection, StreamEvent
from acp.schema import AllowedOutcome, PermissionOption, RequestPermissionResponse, ToolCallUpdate
from amplifier_agent_acp import _agent
import pytest

from tests.support.credentials import anthropic_only


@pytest.fixture(autouse=True)
def default_provider_credentials():
    # An unset provider defaults from credentials; an Anthropic key alone keeps it anthropic.
    # A separate patch, so a test undoing its own monkeypatch keeps these.
    with pytest.MonkeyPatch.context() as patch:
        anthropic_only(patch)
        yield


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    host = tmp_path / "host.json"
    host.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    async def discover():
        # Listing models asks each provider over the network.
        return []

    monkeypatch.setattr(_agent, "discover", discover)
    return host


class Recorder:
    """A client that answers each permission request with `answer`; when it is None, once `release` is set."""

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.release = asyncio.Event()
        # Every message the face sent, in the order it was written.
        self.wire: list[dict[str, Any]] = []
        self._arrived = asyncio.Event()
        # The face's stdin, and the task serving it, once connected.
        self.stdin: asyncio.StreamWriter | None = None
        self.face: asyncio.Task[None] | None = None

    def observe(self, event: StreamEvent) -> None:
        if event.direction == StreamDirection.INCOMING:
            self.wire.append(event.message)
            self._arrived.set()

    async def until(self, found: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        """The first message the face sent that `found` accepts, once it arrives."""
        async with asyncio.timeout(30):
            while True:
                for message in self.wire:
                    if found(message):
                        return message
                self._arrived.clear()
                await self._arrived.wait()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        pass

    async def request_permission(
        self, options: list[PermissionOption], session_id: str, tool_call: ToolCallUpdate, **kwargs: Any
    ) -> RequestPermissionResponse:
        if self.answer is None:
            await self.release.wait()
        assert self.answer is not None
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=self.answer))


@pytest.fixture
def connect():
    """Start the face and connect a recording client to it over a loopback socket pair."""

    @contextlib.asynccontextmanager
    async def connected(answer: str | None = "allow_once") -> AsyncIterator[tuple[ClientSideConnection, Recorder]]:
        accepted: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = asyncio.Future()
        server = await asyncio.start_server(
            lambda reader, writer: accepted.set_result((reader, writer)), host="127.0.0.1", port=0
        )
        host, port = server.sockets[0].getsockname()[:2]
        client_reader, client_writer = await asyncio.open_connection(host, port)
        agent_reader, agent_writer = await accepted
        face = asyncio.create_task(_agent.serve(agent_writer, agent_reader))
        recorder = Recorder(answer)
        recorder.stdin, recorder.face = client_writer, face
        connection = connect_to_agent(
            cast(Client, recorder), client_writer, client_reader, observers=[recorder.observe]
        )
        try:
            yield connection, recorder
        finally:
            await connection.close()
            client_writer.close()
            # The face sees stdin close and shuts down.
            try:
                await asyncio.wait_for(face, 30)
            finally:
                agent_writer.close()
                server.close()
                await server.wait_closed()

    return connected
