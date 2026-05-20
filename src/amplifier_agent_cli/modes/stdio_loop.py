"""Mode B — stdio JSON-RPC loop.

Single entry-point for the stdio (Mode B) transport.  The library is
mode-agnostic: this is the ONLY file that knows about stdin / stdout.
The engine sees only the injected protocol points.

Capability handshake (§6 + Appendix A)
---------------------------------------
The first JSON-RPC message MUST be ``agent/initialize``.  Any other request
that arrives before initialization is rejected with an ``agent_not_ready``
error (code -32002).

Loop responsibilities
---------------------
- Read NDJSON messages from *reader* (typically asyncio stdin).
- First message MUST be ``agent/initialize``.
- Subsequent requests are dispatched to *engine*.
- Server-initiated approval responses route to StdioApprovalSystem.
- Exits on EOF, ``agent/shutdown``, idle timeout, or fatal error.

Response routing
----------------
Incoming messages classified as ``'response'`` are synchronously routed to
``approval.handle_response()``.  The StdioApprovalSystem resolves the
pending Future for the matching request id.

Approval concurrency
--------------------
``turn/submit`` dispatch runs as a background asyncio Task so the loop can
simultaneously drain the reader for incoming approval responses.  This is
required because the engine may call ``approval.request()`` mid-turn, which
suspends until the host sends back an ``approval/request`` response — and the
loop must be reading from the reader to deliver that response.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from amplifier_agent_lib import jsonrpc
from amplifier_agent_lib.protocol_points.defaults_stdio import (
    StdioApprovalSystem,
    StdioDisplaySystem,
    synthesize_result_final_if_needed,
)

# ---------------------------------------------------------------------------
# Protocol interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class _Reader(Protocol):
    """Minimal async reader — typically wraps asyncio.StreamReader.readline."""

    async def readline(self) -> bytes: ...


@runtime_checkable
class _Writer(Protocol):
    """Minimal async writer — typically wraps asyncio.StreamWriter."""

    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...


@runtime_checkable
class _EngineProtocol(Protocol):
    """Subset of the Engine interface consumed by stdio_loop.

    The engine is injected so this module stays transport-agnostic.
    """

    def attach_display(self, display: StdioDisplaySystem) -> None: ...

    def attach_approval(self, approval: StdioApprovalSystem) -> None: ...

    async def initialize(
        self,
        *,
        client_capabilities: Any,
        client_info: Any,
    ) -> dict:  # type: ignore[type-arg]
        """Perform capability negotiation.

        Returns a result dict ready to embed in a JSON-RPC success response.
        Raises on fatal initialization failure.
        """
        ...

    async def dispatch(self, method: str, params: Any) -> Any:
        """Dispatch an already-initialized request."""
        ...


# ---------------------------------------------------------------------------
# Internal helper — dispatch with concurrent approval response routing
# ---------------------------------------------------------------------------


async def _dispatch_with_response_routing(
    engine: _EngineProtocol,
    method: str,
    params: Any,
    reader: _Reader,
    approval: StdioApprovalSystem,
) -> tuple[Any, bool]:
    """Run ``engine.dispatch`` as a background task while routing approval responses.

    ``turn/submit`` can trigger mid-turn approval requests: the engine calls
    ``approval.request()``, which writes an ``approval/request`` JSON-RPC
    request to the writer and suspends awaiting a Future.  The host must send
    back a JSON-RPC response with the matching ``id``.  This helper keeps
    reading from the reader while dispatch is in-flight and calls
    ``approval.handle_response()`` for every incoming ``'response'``-classified
    message, resolving the pending Future so the engine can continue.

    Parameters
    ----------
    engine:
        The engine to dispatch to.
    method:
        The JSON-RPC method name (e.g. ``'turn/submit'``).
    params:
        The method params dict.
    reader:
        Async reader shared with the outer message loop.
    approval:
        The ``StdioApprovalSystem`` instance wired into the engine.

    Returns
    -------
    tuple[Any, bool]
        ``(result, eof_seen)`` where *result* is the value returned by
        ``engine.dispatch()`` and *eof_seen* is ``True`` if an EOF was read
        from the reader during dispatch (so the outer loop can exit cleanly
        rather than trying to read from an already-drained reader).

    Raises
    ------
    Exception
        Any exception raised by ``engine.dispatch()`` is re-raised.
    """
    dispatch_task: asyncio.Task[Any] = asyncio.create_task(engine.dispatch(method, params))
    read_task: asyncio.Task[Any] = asyncio.create_task(jsonrpc.read_message(reader))
    eof_seen: bool = False

    try:
        while not dispatch_task.done():
            if eof_seen:
                # EOF was consumed from the reader — stop reading and wait for
                # dispatch to finish without consuming more from the reader.
                await dispatch_task
                break

            done, _ = await asyncio.wait(
                {dispatch_task, read_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Process a completed read before checking dispatch, so that any
            # approval response that arrived simultaneously with dispatch
            # completion is still routed to the approval system.
            if read_task in done:
                try:
                    msg = read_task.result()
                except Exception:
                    msg = None

                if msg is None:
                    # EOF consumed — signal the outer loop to exit after this turn.
                    eof_seen = True
                elif jsonrpc.classify(msg) == "response":
                    approval.handle_response(msg)

                # Start the next read only if dispatch is still running and no EOF.
                if not dispatch_task.done() and not eof_seen:
                    read_task = asyncio.create_task(jsonrpc.read_message(reader))

            # While condition re-evaluated at the top of the next iteration.

    finally:
        # Cancel any in-flight read task so the outer loop can resume cleanly.
        # In Python 3.10+ asyncio.Queue.get() cancellation is safe: if the
        # task was blocked waiting for an item, the item stays in the queue.
        if not read_task.done():
            read_task.cancel()
            try:
                await read_task
            except (asyncio.CancelledError, Exception):
                pass

    # Propagate any exception raised by dispatch (re-raises on .result()).
    return await dispatch_task, eof_seen


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run(
    *,
    reader: _Reader,
    writer: _Writer,
    engine: _EngineProtocol,
    idle_timeout_s: float = 300.0,
) -> int:
    """Mode B entry-point — stdio JSON-RPC capability handshake + message loop.

    Parameters
    ----------
    reader:
        Async reader producing NDJSON lines (one JSON object per ``readline()``
        call).  Empty bytes signals EOF.
    writer:
        Async writer accepting NDJSON lines.  Must implement ``write()`` and
        ``drain()``.  This is the ONLY place that writes to stdout.
    engine:
        Engine stand-in that handles ``initialize`` and ``dispatch``.  The
        loop injects protocol points via ``attach_display`` / ``attach_approval``
        before the first message is processed.
    idle_timeout_s:
        Seconds of inactivity before the loop exits (default 300 s).

    Returns
    -------
    int
        Exit code — 0 for clean exit (EOF, shutdown, idle timeout),
        1 for fatal error (agent/shutdown dispatch exception).
    """
    # ------------------------------------------------------------------
    # Wire up protocol points (display + approval) into the engine.
    # Both require the writer; approval also requires the display.
    # ------------------------------------------------------------------
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=1_000_000)
    engine.attach_display(display)
    engine.attach_approval(approval)

    initialized: bool = False

    # ------------------------------------------------------------------
    # Message loop
    # ------------------------------------------------------------------
    while True:
        # --- Read next message with idle timeout -------------------------
        try:
            message = await asyncio.wait_for(
                jsonrpc.read_message(reader),
                timeout=idle_timeout_s,
            )
        except TimeoutError:
            # Emit error notification and exit cleanly.
            notification = jsonrpc.make_notification(
                method="error",
                params={
                    "code": "idle_timeout",
                    "message": f"Idle for {idle_timeout_s}s",
                    "recoverable": False,
                },
            )
            await jsonrpc.write_message(writer, notification)
            return 0
        except asyncio.CancelledError:
            return 0

        if message is None:
            # EOF — clean exit.
            return 0

        # --- Classify ----------------------------------------------------
        kind = jsonrpc.classify(message)

        # Response: route to approval system (resolves pending futures).
        if kind == "response":
            approval.handle_response(message)
            continue

        # Notification / invalid: silently ignored.
        if kind != "request":
            continue

        method: str = message.get("method", "")
        msg_id: Any = message.get("id")
        params: Any = message.get("params", {})

        # --- Guard: must initialize first --------------------------------
        if not initialized and method != "agent/initialize":
            error = jsonrpc.make_error(
                id=msg_id,
                code=-32002,
                message="Engine not initialized",
                data={"code": "agent_not_ready"},
            )
            await jsonrpc.write_message(writer, error)
            continue

        # --- Handle agent/initialize -------------------------------------
        if method == "agent/initialize":
            client_capabilities: Any = params.get("capabilities", {}) if isinstance(params, dict) else {}
            client_info: Any = params.get("clientInfo", {}) if isinstance(params, dict) else {}

            try:
                result = await engine.initialize(
                    client_capabilities=client_capabilities,
                    client_info=client_info,
                )
            except Exception:
                error = jsonrpc.make_error(
                    id=msg_id,
                    code=-32603,
                    message="Provider initialization failed",
                    data={"code": "provider_init_failed"},
                )
                await jsonrpc.write_message(writer, error)
                continue

            response = jsonrpc.make_response(id=msg_id, result=result)
            await jsonrpc.write_message(writer, response)
            initialized = True
            continue

        # --- Handle agent/shutdown ---------------------------------------
        if method == "agent/shutdown":
            try:
                result = await engine.dispatch(method, params)
            except Exception:
                # Per JSON-RPC 2.0, generic dispatch failures map to -32603
                # (Internal error), not -32600 (Invalid Request).
                error = jsonrpc.make_error(
                    id=msg_id,
                    code=-32603,
                    message="Internal error",
                    data={"code": "internal"},
                )
                await jsonrpc.write_message(writer, error)
                return 1

            shutdown_result = dict(result) if result else {}
            response = jsonrpc.make_response(id=msg_id, result=shutdown_result)
            await jsonrpc.write_message(writer, response)
            return 0

        # --- Handle turn/submit ------------------------------------------
        if method == "turn/submit":
            display.reset_for_turn()
            try:
                turn_result, eof_seen = await _dispatch_with_response_routing(engine, method, params, reader, approval)
            except Exception:
                error = jsonrpc.make_error(
                    id=msg_id,
                    code=-32603,
                    message="Tool execution failed",
                    data={"code": "tool_execution_failed"},
                )
                await jsonrpc.write_message(writer, error)
                continue

            # L14 safety-net: synthesized result/final appears on wire BEFORE
            # the turn/submit response (Appendix B).
            result_dict = dict(turn_result) if turn_result is not None else {}
            await synthesize_result_final_if_needed(display, reply=result_dict)
            response = jsonrpc.make_response(id=msg_id, result=result_dict)
            await jsonrpc.write_message(writer, response)
            if eof_seen:
                # EOF was consumed inside the dispatch helper — exit cleanly.
                return 0
            continue

        # --- Any other method: pass-through dispatch ----------------------
        try:
            result = await engine.dispatch(method, params)
        except ValueError:
            # Engine.dispatch raises ValueError for unknown method names —
            # that IS JSON-RPC -32601 "Method not found".
            error = jsonrpc.make_error(
                id=msg_id,
                code=-32601,
                message="Method not found",
                data={"code": "wire_protocol_violation"},
            )
            await jsonrpc.write_message(writer, error)
            continue
        except Exception:
            # Any other dispatch exception is a generic internal error.
            error = jsonrpc.make_error(
                id=msg_id,
                code=-32603,
                message="Internal error",
                data={"code": "internal"},
            )
            await jsonrpc.write_message(writer, error)
            continue

        passthrough_result = dict(result) if result is not None else {}
        response = jsonrpc.make_response(id=msg_id, result=passthrough_result)
        await jsonrpc.write_message(writer, response)
