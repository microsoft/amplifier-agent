"""Bidirectional process service for owned language bindings."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import uuid
from collections.abc import Awaitable
from typing import Any

from .._ports import AgentPort, SessionPort, TurnPort, active_turn_id
from .._records import (
    AgentError,
    AgentOptions,
    ApprovalResponse,
    SessionOptions,
    Tool,
    ToolContext,
    ToolFailed,
    ToolOutcomeUnknown,
    TurnInput,
    _ToolNotExecuted,
)
from .._versions import CONTRACT_VERSIONS as contract_versions
from .codec import dumps, loads, record, to_data


def invalid(message: str) -> AgentError:
    return AgentError("invalid_input", "input", message, "Use the declared operation and fields.")


class CreditWindow:
    def __init__(self, credit: int) -> None:
        self.credit = credit
        self.changed = asyncio.Event()

    def add(self, count: int) -> None:
        if count > 0:
            self.credit += count
            self.changed.set()

    async def take(self) -> None:
        while self.credit <= 0:
            self.changed.clear()
            await self.changed.wait()
        self.credit -= 1


class RuntimeServer:
    def __init__(self) -> None:
        self.agents: dict[str, AgentPort] = {}
        self.sessions: dict[str, SessionPort] = {}
        self.turns: dict[str, TurnPort] = {}
        self.windows: dict[str, CreditWindow] = {}
        self.turn_sessions: dict[str, str] = {}
        self.session_agents: dict[str, str] = {}
        self.pump_tasks: dict[str, asyncio.Task[Any]] = {}
        self.callbacks: dict[str, asyncio.Future[Any]] = {}
        self.callback_errors: dict[str, AgentError] = {}
        self.callback_context: dict[str, dict[str, Any]] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        self.pumps: set[asyncio.Task[Any]] = set()
        self.output_lock = asyncio.Lock()
        self.connected = True

    async def send(self, value: Any) -> None:
        if not self.connected:
            return
        encoded = dumps(value) + "\n"
        async with self.output_lock:
            await asyncio.to_thread(self._write, encoded)

    @staticmethod
    def _write(value: str) -> None:
        sys.stdout.write(value)
        sys.stdout.flush()

    def spawn(self, awaitable: Awaitable[Any], *, pump: bool = False) -> asyncio.Task[Any]:
        async def run() -> Any:
            return await awaitable

        task = asyncio.create_task(run())
        registry = self.pumps if pump else self.tasks
        registry.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            registry.discard(done)
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    print(f"Runtime task failed: {type(error).__name__}: {error}", file=sys.stderr)

        task.add_done_callback(finished)
        return task

    async def callback(self, kind: str, args: dict[str, Any]) -> Any:
        callback_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.callbacks[callback_id] = future
        correlation = "call_id" if kind == "tool" else "request_id"
        expected = args.get("context" if kind == "tool" else "request", {}).get(correlation)
        self.callback_context[callback_id] = {correlation: expected}
        await self.send(
            {"event": "callback", "callback_id": callback_id, "turn_id": active_turn_id.get(),
             "kind": kind, "args": args}
        )
        try:
            try:
                reply = await asyncio.shield(future)
            except asyncio.CancelledError:
                await self.send({"event": "callback_cancel", "callback_id": callback_id,
                                 "turn_id": active_turn_id.get()})
                reply = await asyncio.shield(future)
            if callback_id in self.callback_errors:
                raise self.callback_errors[callback_id]
            if expected is not None and reply.get(correlation) != expected:
                raise AgentError(
                    "tool_result_invalid" if kind == "tool" else "approval_invalid",
                    "executor" if kind == "tool" else "approval",
                    "The callback reply has the wrong correlation id.",
                    "Return the correlation id from the corresponding callback request.",
                    correlation_id=expected,
                )
            if "error" in reply:
                error = reply["error"]
                kind = error.get("kind")
                message = error.get("message", "Caller callback failed")
                if kind == "tool_failed":
                    raise ToolFailed(message)
                if kind == "tool_completion_unknown":
                    raise ToolOutcomeUnknown(message)
                if kind == "tool_not_executed":
                    raise _ToolNotExecuted(message)
                if kind == "approval_unavailable":
                    raise AgentError(
                        "approval_unavailable", "approval", message,
                        "Reconnect the approval handler before starting another turn.",
                        correlation_id=expected,
                    )
                raise RuntimeError(message)
            return reply.get("result")
        finally:
            self.callbacks.pop(callback_id, None)
            self.callback_errors.pop(callback_id, None)
            self.callback_context.pop(callback_id, None)

    def options(self, params: dict[str, Any]) -> AgentOptions:
        supplied = params.get("options", {})
        if not isinstance(supplied, dict):
            raise invalid("Agent options must be an object.")
        data = dict(supplied)
        callback_tools = set(params.get("callback_tools", []))
        tools = []
        declarations = data.get("tools", [])
        if not isinstance(declarations, list):
            raise invalid("Tools must be an array.")
        for declaration in declarations:
            if isinstance(declaration, str):
                tools.append(declaration)
                continue
            if not isinstance(declaration, dict):
                raise invalid("Each tool must be an object or a built-in tool name.")
            declaration = dict(declaration)
            name = declaration.get("name")

            async def handler(
                arguments: dict[str, Any], context: ToolContext, tool_name: Any = name
            ) -> Any:
                return await self.callback(
                    "tool",
                    {
                        "name": tool_name,
                        "arguments": arguments,
                        "context": to_data(context),
                    },
                )

            declaration["handler"] = handler if name in callback_tools else None
            tools.append(record(Tool, declaration))
        if "tools" in data:
            data["tools"] = tools
        if params.get("callback_approvals"):

            async def approve(request: Any) -> Any:
                result = await self.callback("approval", {"request": to_data(request)})
                if isinstance(result, dict):
                    try:
                        return record(ApprovalResponse, result)
                    except (TypeError, ValueError):
                        pass
                return result

            data["approvals"] = approve
        return record(AgentOptions, data)

    def session_result(self, session: SessionPort, agent_id: str) -> dict[str, Any]:
        handle_id = str(uuid.uuid4())
        self.sessions[handle_id] = session
        self.session_agents[handle_id] = agent_id
        return {"handle_id": handle_id, "info": session.info, "history": session.history}

    async def forward(
        self, turn: TurnPort, session: SessionPort, started: asyncio.Future[None] | None = None
    ) -> None:
        turn_id = turn.info.turn_id
        window = self.windows[turn_id]
        try:
            async for event in turn.events():
                await window.take()
                frame: dict[str, Any] = {
                    "event": "turn_event",
                    "turn_id": turn_id,
                    "event_data": event,
                }
                if event.type == "terminal":
                    frame["history"] = turn.final_history
                await self.send(frame)
                if event.type == "turn_started" and started is not None and not started.done():
                    started.set_result(None)
        finally:
            if started is not None and not started.done():
                started.set_exception(AgentError(
                    "internal_failed", "internal", "The turn did not produce its first event.",
                    "Close this agent and install compatible execution dependencies.",
                ))
            self.windows.pop(turn_id, None)
            self.turns.pop(turn_id, None)
            self.turn_sessions.pop(turn_id, None)
            self.pump_tasks.pop(turn_id, None)

    async def drain(self, turn_ids: list[str]) -> None:
        for turn_id in turn_ids:
            window = self.windows.get(turn_id)
            if window is not None:
                window.add(sys.maxsize)
        tasks = [self.pump_tasks[turn_id] for turn_id in turn_ids if turn_id in self.pump_tasks]
        if tasks:
            await asyncio.gather(*tasks)

    async def dispatch(self, message: dict[str, Any]) -> None:
        identifier = message.get("id")
        try:
            params = message.get("params", {})
            method = message.get("method")
            if method == "callback.resolve":
                callback_id = params.get("callback_id")
                future = self.callbacks.get(callback_id)
                if future is not None:
                    if future.done():
                        approval = "request_id" in self.callback_context.get(callback_id, {})
                        self.callback_errors[callback_id] = AgentError(
                            "approval_invalid" if approval else "tool_result_invalid",
                            "approval" if approval else "executor",
                            "The callback supplied a second resolution.",
                            "Resolve each callback exactly once.", correlation_id=callback_id,
                        )
                    else:
                        future.set_result(params)
                if identifier is not None:
                    await self.send({"id": identifier, "result": None})
                return
            if method == "turn.ack":
                window = self.windows.get(params.get("turn_id"))
                if window is not None:
                    window.add(params.get("count", 1))
                return
            after: Awaitable[Any] | None = None
            started: asyncio.Future[None] | None = None
            result: Any = None
            if method == "hello":
                required = params.get("contract_versions", [])
                if not isinstance(required, list) or not set(required).issubset(contract_versions):
                    raise AgentError(
                        "contract_version_mismatch",
                        "lifecycle",
                        "The runtime cannot present the requested contracts.",
                        "Install matching library and runtime artifacts.",
                        details={"available": list(contract_versions)},
                    )
                result = {"contract_versions": contract_versions}
            elif method == "agent.create":
                from .._engine.assembly import create_engine

                agent = await create_engine(self.options(params))
                agent_id = str(uuid.uuid4())
                self.agents[agent_id] = agent
                result = {"agent_id": agent_id}
            elif method == "agent.create_session":
                options = params.get("options")
                session = await self.agents[params["agent_id"]].create_session(
                    record(SessionOptions, options) if options is not None else None,
                )
                result = self.session_result(session, params["agent_id"])
            elif method == "agent.resume_session":
                session = await self.agents[params["agent_id"]].resume_session(params["session_id"])
                result = self.session_result(session, params["agent_id"])
            elif method == "agent.list_sessions":
                result = await self.agents[params["agent_id"]].list_sessions()
            elif method == "agent.delete_session":
                await self.agents[params["agent_id"]].delete_session(params["session_id"])
            elif method == "agent.close":
                await self.agents[params["agent_id"]].close()
                await self.drain(
                    [
                        turn_id
                        for turn_id, session_id in self.turn_sessions.items()
                        if self.session_agents.get(session_id) == params["agent_id"]
                    ]
                )
                for handle_id in [
                    handle_id
                    for handle_id, agent_id in self.session_agents.items()
                    if agent_id == params["agent_id"]
                ]:
                    self.sessions.pop(handle_id, None)
                    self.session_agents.pop(handle_id, None)
            elif method == "session.start_turn":
                session = self.sessions[params["handle_id"]]
                turn = await session.start_turn(record(TurnInput, params["input"]))
                self.turns[turn.info.turn_id] = turn
                self.turn_sessions[turn.info.turn_id] = params["handle_id"]
                self.windows[turn.info.turn_id] = CreditWindow(params.get("event_window", 64))
                result = {"info": turn.info}
                started = asyncio.get_running_loop().create_future()
                after = self.forward(turn, session, started)
            elif method == "session.run":
                session = self.sessions[params["handle_id"]]
                turn_result, history = await session.run_with_history(
                    record(TurnInput, params["input"])
                )
                result = {"result": turn_result, "history": history}
            elif method == "session.fork":
                handle_id = params["handle_id"]
                result = self.session_result(
                    await self.sessions[handle_id].fork(), self.session_agents[handle_id]
                )
            elif method == "session.close":
                session = self.sessions.get(params["handle_id"])
                if session is not None:
                    await session.close()
                await self.drain(
                    [
                        turn_id
                        for turn_id, session_id in self.turn_sessions.items()
                        if session_id == params["handle_id"]
                    ]
                )
                self.sessions.pop(params["handle_id"], None)
                self.session_agents.pop(params["handle_id"], None)
            elif method == "turn.cancel":
                turn = self.turns.get(params["turn_id"])
                if turn is not None:
                    cancelling = asyncio.create_task(turn.cancel())
                    await asyncio.sleep(0)
                    if getattr(turn, "cancelled", False):
                        await self.send({"event": "cancel_accepted", "turn_id": params["turn_id"]})
                    await cancelling
                await self.drain([params["turn_id"]])
            else:
                raise invalid(f"Unknown operation {method!r}.")
            if after is not None:
                self.pump_tasks[result["info"].turn_id] = self.spawn(after, pump=True)
                if started is not None:
                    await started
            await self.send({"id": identifier, "result": result})
        except AgentError as error:
            await self.send({"id": identifier, "error": error})
        except (KeyError, TypeError, ValueError) as error:
            await self.send({"id": identifier, "error": invalid(str(error))})
        except Exception as error:
            await self.send(
                {
                    "id": identifier,
                    "error": AgentError(
                        "internal_failed",
                        "internal",
                        "The runtime could not complete the operation.",
                        "Inspect service diagnostics and recreate the agent.",
                        details={"exception": type(error).__name__, "message": str(error)},
                    ),
                }
            )

    async def serve(self) -> None:
        reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: protocol, sys.stdin
        )
        try:
            while line := await reader.readline():
                try:
                    message = loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        raise ValueError("An operation must be an object.")
                    self.spawn(self.dispatch(message))
                except (ValueError, UnicodeError) as error:
                    await self.send({"error": invalid(str(error))})
        finally:
            self.connected = False
            transport.close()
            for callback_id, future in list(self.callbacks.items()):
                if not future.done():
                    future.set_result(
                        {
                            **self.callback_context.get(callback_id, {}),
                            "error": {
                                "kind": "approval_unavailable"
                                if "request_id" in self.callback_context.get(callback_id, {})
                                else "tool_completion_unknown",
                                "message": "Caller connection closed.",
                            }
                        }
                    )
            for agent in self.agents.values():
                with contextlib.suppress(Exception):
                    await agent.close()
            for task in list(self.pumps | self.tasks):
                task.cancel()
            await asyncio.gather(*self.pumps, *self.tasks, return_exceptions=True)
