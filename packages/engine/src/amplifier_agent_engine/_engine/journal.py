"""Live event delivery with a bounded memory spool independent of controls."""

from __future__ import annotations

import asyncio
import pickle
from collections.abc import AsyncIterator
from tempfile import SpooledTemporaryFile

from .._records import AgentError, Event


class EventJournal:
    def __init__(self) -> None:
        self._file = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        self._length = 0
        self._available = asyncio.Event()
        self._consumed = False
        self._abandoned = False

    def append(self, event: Event) -> None:
        if self._abandoned:
            return
        self._file.seek(self._length)
        pickle.dump(event, self._file, protocol=5)
        self._length = self._file.tell()
        self._available.set()

    def events(self) -> AsyncIterator[Event]:
        if self._consumed:
            raise AgentError(
                "stream_already_consumed",
                "turn",
                "This stream already has a consumer.",
                "Continue using the original event iterator.",
            )
        self._consumed = True
        return self._read()

    async def _read(self) -> AsyncIterator[Event]:
        position = 0
        try:
            while True:
                if position == self._length:
                    self._available.clear()
                    await self._available.wait()
                self._file.seek(position)
                event = pickle.load(self._file)
                position = self._file.tell()
                if event.type == "terminal":
                    self._file.close()
                    yield event
                    return
                yield event
        finally:
            self._abandoned = True
            self._file.close()
