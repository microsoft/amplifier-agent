"""Python handles for the contracted agent operations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .._ports import AgentPort, SessionPort, TurnPort
from .._records import (
    AgentOptions,
    Event,
    SessionOptions,
    SessionRecord,
    TurnInfo,
    TurnInput,
    TurnRecord,
    TurnResult,
)


class Agent:
    _port: AgentPort

    def __init__(self) -> None:
        raise TypeError("Use await create_agent(options) to create an agent.")

    async def create_session(self, options: SessionOptions | None = None) -> Session:
        return _session(await self._port.create_session(options))

    async def resume_session(self, session_id: str) -> Session:
        return _session(await self._port.resume_session(session_id))

    async def list_sessions(self) -> list[SessionRecord]:
        return await self._port.list_sessions()

    async def delete_session(self, session_id: str) -> None:
        await self._port.delete_session(session_id)

    async def close(self) -> None:
        await self._port.close()

    async def __aenter__(self) -> Agent:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


class Session:
    _port: SessionPort

    def __init__(self) -> None:
        raise TypeError("Use await agent.create_session(options) to create a session.")

    @property
    def info(self) -> SessionRecord:
        return self._port.info

    @property
    def history(self) -> list[TurnRecord]:
        return self._port.history

    async def run(self, input: TurnInput) -> TurnResult:
        return await self._port.run(input)

    async def start_turn(self, input: TurnInput) -> Turn:
        handle = object.__new__(Turn)
        handle._port = await self._port.start_turn(input)
        return handle

    async def fork(self) -> Session:
        return _session(await self._port.fork())

    async def close(self) -> None:
        await self._port.close()

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


class Turn:
    _port: TurnPort

    def __init__(self) -> None:
        raise TypeError("Use await session.start_turn(input) to create a turn.")

    @property
    def info(self) -> TurnInfo:
        return self._port.info

    def events(self) -> AsyncIterator[Event]:
        return self._port.events()

    async def cancel(self) -> None:
        await self._port.cancel()


async def create_agent(options: AgentOptions) -> Agent:
    from ._factory import connect

    handle = object.__new__(Agent)
    handle._port = await connect(options)
    return handle


def _session(port: SessionPort) -> Session:
    handle = object.__new__(Session)
    handle._port = port
    return handle
