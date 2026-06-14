"""Synchronous convenience wrapper around the async core.

For Python hosts that don't use asyncio (Django, Flask, scripts, notebooks,
Jupyter kernels), :func:`spawn_agent_sync` returns a ``SyncSessionHandle``
that runs the async core on a dedicated event loop owned by the handle.

The handle is also a context manager — recommended usage::

    from amplifier_agent_py import spawn_agent_sync

    with spawn_agent_sync(session_id="my-session") as handle:
        for event in handle.submit("Hello, agent!"):
            if event.type == "result":
                print(event.text)
                break

Each call into the handle (``submit``, ``cancel``, ``dispose``) pumps the
owned event loop with ``run_until_complete``.  The activity ticker and
NDJSON stderr parser run between yields of ``submit()`` because the loop
advances each time the next event is requested.

This is the simple model the design committed to (one loop per handle,
``asyncio.run``-style pumping per call).  Hosts already on an event loop
should use :func:`spawn_agent` instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

from ._api import spawn_agent
from .argv_builder import DisplayMode
from .session import SessionHandle
from .types import DisplayEvent, EngineInfo, McpServerConfig


class SyncSessionHandle:
    """Synchronous facade over :class:`SessionHandle`.

    Owns a dedicated ``asyncio`` event loop.  Each method pumps the loop
    until completion.  ``close()`` (or context-manager exit) shuts down
    the engine subprocess and closes the loop.
    """

    def __init__(self, async_handle: SessionHandle, loop: asyncio.AbstractEventLoop) -> None:
        self._async_handle = async_handle
        self._loop = loop
        self._closed = False

    def get_engine_info(self) -> EngineInfo:
        """Return resolved engine metadata (mirrors async ``get_engine_info``)."""
        return self._async_handle.get_engine_info()

    def submit(self, prompt: str) -> Iterator[DisplayEvent]:
        """Submit a prompt and synchronously iterate ``DisplayEvent`` values.

        One-shot per session (D10): a second call raises ``AaaError``.
        Iteration pumps the loop on each ``__next__`` call so the activity
        ticker and stderr NDJSON parser advance between yields.

        Early break is safe: when the caller exits the ``for`` loop before
        the iterator is exhausted (e.g. ``break`` on the ``result`` event),
        the inner ``try`` / ``finally`` drives ``aclose()`` on the underlying
        async generator. Without this, garbage-collection would surface
        ``RuntimeError: async generator ignored GeneratorExit`` and the
        engine subprocess's background tasks (activity ticker, NDJSON
        parser, MCP spill-file cleanup) would skip teardown.
        """
        agen = self._async_handle.submit(prompt).__aiter__()

        def _gen() -> Iterator[DisplayEvent]:
            try:
                while True:
                    try:
                        ev = self._loop.run_until_complete(agen.__anext__())
                    except StopAsyncIteration:
                        return
                    yield ev
            finally:
                # Explicitly close the async generator on every exit path —
                # early break, exception, or normal exhaustion. ``aclose()``
                # is idempotent so this is safe even after StopAsyncIteration.
                # The declared return type of ``submit()`` is ``AsyncIterator``
                # which does not formally expose ``aclose``; the concrete
                # implementation is always an async generator, so we look up
                # the method dynamically to satisfy both contracts.
                # ``SessionHandle.submit()`` is declared to return
                # ``AsyncIterator`` for surface stability, but the concrete
                # implementation is always an async generator. Look up the
                # ``aclose`` method through ``Any`` so static analysis stays
                # honest about the declared interface while the runtime
                # call still reaches the real generator method.
                concrete: Any = agen
                aclose = getattr(concrete, "aclose", None)
                if aclose is not None:
                    try:
                        self._loop.run_until_complete(aclose())
                    except Exception:
                        # Best-effort cleanup; never leak a teardown
                        # exception into caller code that was just trying
                        # to iterate.
                        pass

        return _gen()

    def cancel(self) -> None:
        """Cancel the running subprocess (mirrors async ``cancel``)."""
        if self._closed:
            return
        self._loop.run_until_complete(self._async_handle.cancel())

    def dispose(self) -> None:
        """Graceful shutdown — alias for ``cancel()``."""
        self.cancel()

    def close(self) -> None:
        """Cancel the subprocess (if any) and close the owned event loop.

        Idempotent.  After ``close()``, the handle cannot be used.
        """
        if self._closed:
            return
        try:
            self._loop.run_until_complete(self._async_handle.dispose())
        finally:
            self._loop.close()
            self._closed = True

    def __enter__(self) -> SyncSessionHandle:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def spawn_agent_sync(
    *,
    lifecycle: str = "one-shot",
    session_id: str,
    resume: bool = False,
    cwd: str | None = None,
    env: dict[str, Any] | None = None,
    approval: dict[str, Any] | None = None,
    display: dict[str, Any] | None = None,
    display_mode: DisplayMode | None = None,
    workspace: str | None = None,
    mcp_servers: dict[str, McpServerConfig | dict[str, Any]] | None = None,
    timeout_ms: int | None = None,
    config_path: str | None = None,
    allow_protocol_skew: bool = False,
    _binary_resolver: Callable[[], str] | None = None,
    _engine_version_probe: Callable[[], Any] | None = None,
) -> SyncSessionHandle:
    """Synchronous counterpart of :func:`spawn_agent`.

    Owns a dedicated event loop for the lifetime of the returned handle.
    Recommended for use as a context manager so the loop is always closed.

    All arguments mirror :func:`spawn_agent`.  See that function's docstring
    for the full description.
    """
    loop = asyncio.new_event_loop()
    try:
        async_handle = loop.run_until_complete(
            spawn_agent(
                lifecycle=lifecycle,
                session_id=session_id,
                resume=resume,
                cwd=cwd,
                env=env,
                approval=approval,
                display=display,
                display_mode=display_mode,
                workspace=workspace,
                mcp_servers=mcp_servers,
                timeout_ms=timeout_ms,
                config_path=config_path,
                allow_protocol_skew=allow_protocol_skew,
                _binary_resolver=_binary_resolver,
                _engine_version_probe=_engine_version_probe,
            )
        )
    except BaseException:
        loop.close()
        raise

    return SyncSessionHandle(async_handle, loop)
