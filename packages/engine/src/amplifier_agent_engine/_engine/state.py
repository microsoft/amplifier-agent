"""Single-owner lifecycle and terminal transitions."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal, localcontext
from typing import Any, cast

from .._records import (
    AgentError,
    Event,
    OutputDelta,
    ReasoningDelta,
    ReasoningFinal,
    Selection,
    SessionOptions,
    SessionRecord,
    TextPart,
    Tool,
    ToolResolution,
    ToolResultEvent,
    TurnInfo,
    TurnInput,
    TurnRecord,
    TurnResult,
    TurnStarted,
    Usage,
    UsageEntry,
    UsageEvent,
)
from .._versions import CONTRACT_VERSIONS
from .configuration import ResolvedConfig, select, session_options, turn_input
from .effects import PolicyStop, execute_tool
from .journal import EventJournal
from .ports import Runtime


def closed() -> AgentError:
    return AgentError(
        "closed",
        "lifecycle",
        "This handle is closed.",
        "Create a new agent or session before doing work.",
    )


def unavailable(operation: str) -> AgentError:
    return AgentError(
        "engine_unavailable",
        "lifecycle",
        f"The {operation} operation cannot be provided by this installation.",
        "Install a distribution providing this lifecycle operation.",
        details={"operation": operation},
    )


async def settled(task: asyncio.Task[Any]) -> Any:
    """Keep owned cleanup alive and await it before propagating caller cancellation."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result


class EngineAgent:
    contract_versions = CONTRACT_VERSIONS

    def __init__(
        self,
        config: ResolvedConfig,
        ready: Runtime,
        runtime_factory: Callable[[], Awaitable[Runtime]],
    ) -> None:
        self.config = config
        self._ready: Runtime | None = ready
        self._runtime_factory = runtime_factory
        self._sessions: dict[str, EngineSession] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def _check(self) -> None:
        if self._closed:
            raise closed()

    async def create_session(self, options: SessionOptions | None = None) -> EngineSession:
        self._check()
        value = session_options(options)
        model = select(value.model, self.config.model)
        session_id = value.session_id or str(uuid.uuid4())
        async with self._lock:
            self._check()
            if session_id in self._sessions:
                raise AgentError(
                    "already_exists",
                    "session",
                    "The session id already exists.",
                    "Create a session with a different id.",
                )
            runtime = self._ready
            if runtime is None:
                runtime = await self._runtime_factory()
            else:
                self._ready = None
            if self._closed:
                await runtime.close()
                raise closed()
            session = EngineSession(self, runtime, SessionRecord(session_id, "ephemeral"), model)
            self._sessions[session_id] = session
            return session

    async def resume_session(self, session_id: str) -> EngineSession:
        self._check()
        raise unavailable("resume_session")

    async def list_sessions(self) -> list[SessionRecord]:
        self._check()
        raise unavailable("list_sessions")

    async def delete_session(self, session_id: str) -> None:
        self._check()
        raise unavailable("delete_session")

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await settled(self._close_task)

    async def _close(self) -> None:
        async with self._lock:
            tasks = [session.close() for session in tuple(self._sessions.values())]
            if self._ready is not None:
                tasks.append(self._ready.close())
                self._ready = None
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result


class EngineSession:
    def __init__(
        self, agent: EngineAgent, runtime: Runtime, info: SessionRecord, model: str
    ) -> None:
        self.agent, self.runtime, self._info, self.model = agent, runtime, info, model
        self._history: list[TurnRecord] = []
        self._accepted = False
        self._active: EngineTurn | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def _check(self) -> None:
        if self._closed or self.agent._closed:
            raise closed()

    @property
    def info(self) -> SessionRecord:
        self._check()
        return self._info

    @property
    def history(self) -> list[TurnRecord]:
        self._check()
        return copy.deepcopy(self._history)

    async def start_turn(self, input: TurnInput) -> EngineTurn:
        self._check()
        if self._active is not None:
            raise AgentError(
                "busy",
                "turn",
                "A turn is already active in this session.",
                "Wait for the active turn's terminal event.",
            )
        value = turn_input(input, seed_allowed=not self._accepted)
        model = select(value.model, self.model)
        turn = EngineTurn(self, value, model)
        self._active = turn
        self._accepted = True
        turn.start()
        return turn

    async def run(self, input: TurnInput) -> TurnResult:
        result, _ = await self.run_with_history(input)
        return result

    async def run_with_history(self, input: TurnInput) -> tuple[TurnResult, list[TurnRecord]]:
        turn = await self.start_turn(input)
        async for event in turn.events():
            if event.type == "terminal":
                return cast(TurnResult, event.payload), turn.final_history
        raise AssertionError("A completed event journal must contain a terminal event")

    async def fork(self) -> EngineSession:
        self._check()
        if self._active is not None:
            raise AgentError(
                "busy", "turn", "A turn is active.", "Wait for terminal before forking."
            )
        raise unavailable("fork")

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await settled(self._close_task)

    async def _close(self) -> None:
        if self._active is not None:
            await self._active.cancel()
        try:
            await self.runtime.close()
        finally:
            self.agent._sessions.pop(self._info.session_id, None)


class EngineTurn:
    def __init__(self, session: EngineSession, input: TurnInput, model: str) -> None:
        self.session, self.input, self.model = session, input, model
        self._info = TurnInfo(session._info.session_id, str(uuid.uuid4()))
        self._journal = EventJournal()
        self._sequence = 0
        self._parts: list[TextPart] = []
        self._reasoning: list[str] = []
        self._usage: dict[tuple[str, str], UsageEntry] = {}
        self._requests: dict[tuple[str, str], int] = {}
        self._known: dict[tuple[str, str, str], int] = {}
        self._result: TurnResult | None = None
        self._policy: TurnResult | None = None
        self.pending: set[asyncio.Task[Any]] = set()
        self.cancelled = False
        self._entered = asyncio.Event()
        self._task: asyncio.Task[None]
        self._cancel_task: asyncio.Task[None] | None = None
        self._final_history: list[TurnRecord] = []
        self._resolutions: list[ToolResolution] = []

    @property
    def info(self) -> TurnInfo:
        self.session._check()
        return self._info

    @property
    def final_history(self) -> list[TurnRecord]:
        return copy.deepcopy(self._final_history)

    def start(self) -> None:
        self._task = asyncio.create_task(self._execute())

    def emit(self, name: str, payload: Any) -> None:
        if self._result is not None:
            return
        if name == "tool_result" and isinstance(payload, ToolResultEvent):
            self._resolutions.append(copy.deepcopy(payload.resolution))
        self._sequence += 1
        self._journal.append(
            Event(
                "turn-events/1",
                self._info.session_id,
                self._info.turn_id,
                self._sequence,
                name,
                copy.deepcopy(payload),
            )
        )

    def output(self, text: str) -> None:
        part = TextPart(text)
        self._parts.append(part)
        self.emit("output_delta", OutputDelta([part]))

    def extension(self, name: str, payload: Any, fields: dict[str, Any]) -> None:
        if self._result is not None:
            return
        self._sequence += 1
        event = Event(
            "turn-events/1",
            self._info.session_id,
            self._info.turn_id,
            self._sequence,
            name,
            copy.deepcopy(payload),
        )
        for key, value in fields.items():
            setattr(event, key, copy.deepcopy(value))
        self._journal.append(event)

    def reasoning(self, text: str, *, final: bool = False) -> None:
        if final:
            self.emit("reasoning_final", ReasoningFinal("".join(self._reasoning)))
            self._reasoning.clear()
        else:
            self._reasoning.append(text)
            self.emit("reasoning_delta", ReasoningDelta(text))

    def work_started(self) -> None:
        key = (self.session.agent.config.provider, self.model)
        self._usage.setdefault(key, UsageEntry(*key))
        self._requests[key] = self._requests.get(key, 0) + 1

    def usage(self, entry: UsageEntry) -> None:
        key = (entry.provider, entry.model)
        current = self._usage.setdefault(key, UsageEntry(*key))
        for name in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"):
            count = getattr(entry, name)
            if count is not None:
                prior = getattr(current, name)
                setattr(current, name, (prior if prior is not None else 0) + count)
                known_key = (*key, name)
                self._known[known_key] = self._known.get(known_key, 0) + 1
        if entry.cost is not None:
            if current.cost is None:
                current.cost = {}
            for currency, amount in entry.cost.items():
                prior = current.cost.get(currency, Decimal(0))
                with localcontext() as context:
                    scale = min(int(prior.as_tuple().exponent), int(amount.as_tuple().exponent))
                    context.prec = max(28, max(prior.adjusted(), amount.adjusted()) - scale + 2)
                    current.cost[currency] = prior + amount
                known_key = (*key, f"cost.{currency}")
                self._known[known_key] = self._known.get(known_key, 0) + 1

    def stop(self, state: str, error: AgentError) -> PolicyStop:
        if self._policy is None:
            self._policy = TurnResult(cast(Any, state), error=error)
        return PolicyStop()

    def fail(self, error: Exception) -> None:
        if isinstance(error, AgentError):
            self.stop("failure", error)

    async def call_tool(self, tool: Tool, call_id: str, arguments: dict[str, Any]) -> str:
        return await execute_tool(self, self.session.agent.config, tool, call_id, arguments)

    async def _execute(self) -> None:
        self._entered.set()
        self.emit(
            "turn_started",
            TurnStarted("fresh", Selection(self.session.agent.config.provider, self.model)),
        )
        result = TurnResult("success")
        try:
            if self.cancelled:
                raise asyncio.CancelledError
            await self.session.runtime.execute(self.input, self)
        except (asyncio.CancelledError, PolicyStop):
            result = self._policy or TurnResult("cancelled", error=self._cancel_error())
        except Exception:
            result = self._policy or TurnResult(
                "failure",
                error=AgentError(
                    "provider_failed",
                    "provider",
                    "The provider request failed.",
                    "Check provider credentials, availability, and request compatibility before starting another turn.",
                ),
            )
        finally:
            await settled(asyncio.create_task(self._finish(result)))

    async def _finish(self, result: TurnResult) -> None:
        remaining = [task for task in self.pending if not task.done()]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        try:
            await self.session.runtime.settle(self._resolutions)
        except Exception:
            self.stop(
                "failure",
                AgentError(
                    "internal_failed",
                    "internal",
                    "The turn's conversation could not be settled.",
                    "Close this session and create a new one before doing more work.",
                ),
            )
        if self._policy is not None:
            result = self._policy
        if self.cancelled:
            result = TurnResult("cancelled", error=self._cancel_error())
        if self._reasoning:
            self.reasoning("", final=True)
        result.content = copy.deepcopy(self._parts)
        if self._usage:
            entries = copy.deepcopy(list(self._usage.values()))
            for entry in entries:
                key = (entry.provider, entry.model)
                for name in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"):
                    if self._known.get((*key, name), 0) != self._requests[key]:
                        setattr(entry, name, None)
                if entry.cost is not None:
                    entry.cost = {
                        currency: value
                        for currency, value in entry.cost.items()
                        if self._known.get((*key, f"cost.{currency}"), 0) == self._requests[key]
                    } or None
            result.usage = Usage(entries)
            self.emit("usage", UsageEvent(result.usage))
        self.emit("terminal", result)
        self._result = result
        self.session._history.append(
            TurnRecord(self._info.turn_id, copy.deepcopy(self.input), copy.deepcopy(result))
        )
        self._final_history = list(self.session._history)
        self.session._active = None

    def _cancel_error(self) -> AgentError:
        return AgentError(
            "turn_cancelled",
            "turn",
            "The caller cancelled the turn.",
            "Start another turn when ready.",
            correlation_id=self._info.turn_id,
        )

    def events(self) -> AsyncIterator[Event]:
        return self._journal.events()

    async def cancel(self) -> None:
        if self._task.done():
            return
        if self._cancel_task is None:
            self.cancelled = True
            self.session.runtime.request_cancel()
            self._cancel_task = asyncio.create_task(self._cancel())
        await settled(self._cancel_task)

    async def _cancel(self) -> None:
        await self._entered.wait()
        remaining = [task for task in self.pending if not task.done()]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
