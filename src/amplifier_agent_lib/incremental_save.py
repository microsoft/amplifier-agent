"""Incremental transcript save hook for the AAA engine (A2 — CR-1).

``IncrementalSaveHook`` is a kernel ``tool:post`` handler that persists the
current transcript to a :class:`SessionStore` after every tool invocation.

Design reference: §4.6.

Hook contract
-------------
Handlers are plain async callables registered via
``coordinator.hooks.register('tool:post', handler, name=...)``.
The expected signature is::

    async def handler(event: str, data: dict[str, Any]) -> HookResult

This class implements ``__call__`` with that signature so an instance can be
registered directly.

Note: :meth:`SessionStore.save` is **synchronous** — it is not awaited.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from amplifier_core.models import HookResult

from amplifier_agent_lib.session_store import SessionStore


class IncrementalSaveHook:
    """Persist transcript on every ``tool:post`` event."""

    def __init__(
        self,
        *,
        store: SessionStore,
        session_id: str,
        get_messages: Callable[[], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._get_messages = get_messages

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        transcript = await self._get_messages()
        tool_name = data.get("tool_name") or data.get("tool") or ""
        self._store.save(
            self._session_id,
            transcript,
            metadata={"last_tool": tool_name},
        )
        return HookResult(action="continue")
