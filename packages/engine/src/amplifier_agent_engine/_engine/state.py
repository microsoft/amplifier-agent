"""Single-owner lifecycle and terminal transitions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import copy
from dataclasses import dataclass
from decimal import Decimal, localcontext
import shutil
from typing import Any, cast
import uuid

from amplifier_agent_engine._engine import reasoning, selection
from amplifier_agent_engine._engine.configuration import ResolvedConfig, session_options, turn_input
from amplifier_agent_engine._engine.effects import PolicyStop, RecoveryState, execute_tool
from amplifier_agent_engine._engine.images import (
    conversation_inputs,
    copilot_drops_images,
    copilot_unsupported,
    holds_image,
    image_unsupported,
)
from amplifier_agent_engine._engine.journal import EventJournal
from amplifier_agent_engine._engine.ports import Runtime
from amplifier_agent_engine._engine.provider_policy import PROVIDERS, named
from amplifier_agent_engine._engine.storage import (
    CommittedTurn,
    SessionLease,
    SessionStore,
    now,
    session_error,
    storage_error,
)
from amplifier_agent_engine._ports import active_turn_id
from amplifier_agent_engine._records import (
    AgentError,
    ContentPart,
    Event,
    OutputDelta,
    Progress,
    ReasoningDelta,
    ReasoningFinal,
    Selection,
    SessionOptions,
    SessionRecord,
    TextPart,
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
from amplifier_agent_engine._versions import CONTRACT_VERSIONS

RuntimeFactory = Callable[..., Awaitable[Runtime]]


@dataclass
class Branch:
    """What a fork carries into its child: the committed conversation as of the fork."""

    parent_id: str
    messages: list[dict[str, Any]]
    history: list[TurnRecord]
    committed: list[CommittedTurn]
    inherited: bool


def closed() -> AgentError:
    return AgentError(
        "closed",
        "lifecycle",
        "This handle is closed.",
        "Create a new agent or session before doing work.",
    )


def selected(config: ResolvedConfig, model: str, effort: str | None) -> dict[str, Any]:
    """The session facts that record a selection; ``effort`` is the named value, if any."""
    return {"provider": config.provider, "model": model, "reasoning_effort": effort}


def transcript(config: ResolvedConfig, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "provider": config.provider, "messages": messages}


def busy(remedy: str) -> AgentError:
    return AgentError("busy", "turn", "A turn is already active in this session.", remedy)


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

    def __init__(self, config: ResolvedConfig, runtime_factory: RuntimeFactory) -> None:
        self.config = config
        self._runtime_factory = runtime_factory
        self._sessions: dict[str, EngineSession] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._store = SessionStore(config.sessions_directory)
        self._capabilities: dict[tuple[str, str], frozenset[str] | None] = {}

    def _check(self) -> None:
        if self._closed:
            raise closed()

    async def model_capabilities(self, runtime: Runtime, provider: str, model: str) -> frozenset[str] | None:
        """What the provider reports about ``model``, asked once per agent."""
        key = (provider, model)
        if key not in self._capabilities:
            self._capabilities[key] = await runtime.model_capabilities(model)
        return self._capabilities[key]

    async def create_session(self, options: SessionOptions | None = None) -> EngineSession:
        self._check()
        value = session_options(options)
        model = named(value.model, self.config.model)
        effort = value.reasoning_effort or self.config.reasoning_effort
        reasoning.check(self.config.provider, model, effort)
        session_id = value.session_id or str(uuid.uuid4())
        async with self._lock:
            self._check()
            if session_id in self._sessions:
                raise session_error("already_exists")
            return await self._create(session_id, value.persistence, self.config, model, reasoning_effort=effort)

    def _facts(self, config: ResolvedConfig, model: str, effort: str | None, inherited: bool) -> dict[str, Any]:
        return {
            "working_dir": str(self.config.working_directory),
            **selected(config, model, effort),
            "inherited": inherited,
        }

    async def _runtime(self, session_id: str, resumed: bool, config: ResolvedConfig) -> Runtime:
        if config is self.config:
            return await self._runtime_factory(session_id, None, resumed)
        return await self._runtime_factory(session_id, None, resumed, selected=config)

    async def _create(
        self,
        session_id: str,
        persistence: Any,
        config: ResolvedConfig,
        model: str,
        branch: Branch | None = None,
        reasoning_effort: str | None = None,
    ) -> EngineSession:
        lease = None
        runtime = None
        reserved = False
        if persistence == "durable":
            lease = self._store.create_lease(session_id)
        elif self._store.exists(session_id):
            raise session_error("already_exists")
        try:
            if lease is not None:
                inherited = branch is not None and branch.inherited
                facts = self._facts(config, model, reasoning_effort, inherited)
                if branch is not None:
                    facts.update(parent_id=branch.parent_id, forked_at=now())
                self._store.reserve(
                    session_id,
                    facts,
                    copy.deepcopy(branch.messages) if branch is not None else [],
                    copy.deepcopy(branch.committed) if branch is not None else [],
                )
                reserved = True
            runtime = await self._runtime(session_id, False, config)
            if branch is not None:
                await runtime.restore(transcript(config, copy.deepcopy(branch.messages)))
            self._check()
            session = EngineSession(
                self,
                runtime,
                SessionRecord(session_id, persistence, config.provider, model),
                model,
                lease=lease,
                reasoning_effort=reasoning_effort,
                config=config,
            )
            if branch is not None:
                session._history = copy.deepcopy(branch.history)
                session._committed = copy.deepcopy(branch.committed)
                session._inherited = branch.inherited
                session._continuation = "resumed" if branch.inherited else "fresh"
            self._sessions[session_id] = session
            return session
        except BaseException:
            try:
                if runtime is not None:
                    await settled(asyncio.create_task(runtime.close()))
            finally:
                try:
                    if reserved:
                        shutil.rmtree(self._store.session_dir(session_id), ignore_errors=True)
                finally:
                    if lease is not None:
                        lease.close()
            raise

    async def resume_session(self, session_id: str) -> EngineSession:
        self._check()
        async with self._lock:
            self._check()
            lease, saved = self._store.resume(session_id)
            runtime = None
            try:
                provider, model = saved.metadata.get("provider"), saved.metadata.get("model")
                effort = saved.metadata.get("reasoning_effort")
                if not isinstance(provider, str) or not isinstance(model, str) or not model:
                    raise storage_error()
                if effort is not None and effort not in reasoning.EFFORTS:
                    raise storage_error()
                if provider not in PROVIDERS:
                    raise selection.unavailable(provider)
                # The saved selection, not this agent's, carries on.
                config = selection.configure(self.config, provider, model)
                runtime = await self._runtime(session_id, True, config)
                await runtime.restore(transcript(config, saved.messages))
                self._check()
                session = EngineSession(
                    self,
                    runtime,
                    SessionRecord(session_id, "durable", provider, model),
                    model,
                    lease=lease,
                    reasoning_effort=effort,
                    config=config,
                )
                session._history = [copy.deepcopy(item.turn) for item in saved.turns]
                session._committed = saved.turns
                session._accepted = bool(saved.turns)
                session._inherited = saved.metadata.get("inherited") is True
                session._continuation = "resumed"
                self._sessions[session_id] = session
                return session
            except BaseException:
                try:
                    if runtime is not None:
                        await settled(asyncio.create_task(runtime.close()))
                finally:
                    lease.close()
                raise

    async def list_sessions(self) -> list[SessionRecord]:
        self._check()
        records = []
        for session_id in self._store.list_ids():
            facts = self._store.facts(session_id)
            provider, model, effort = facts.get("provider"), facts.get("model"), facts.get("reasoning_effort")
            if not isinstance(provider, str) or not isinstance(model, str) or effort not in (None, *reasoning.EFFORTS):
                raise AgentError(
                    "internal_failed",
                    "session",
                    f"The saved session {session_id!r} records no valid selection.",
                    f"Delete session {session_id!r}, or restore its metadata.json, before listing sessions.",
                    details={"session_id": session_id},
                )
            records.append(SessionRecord(session_id, "durable", provider, model, effort or reasoning.DEFAULT))
        return records

    async def delete_session(self, session_id: str) -> None:
        self._check()
        self._store.delete(session_id)

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await settled(self._close_task)

    async def _close(self) -> None:
        async with self._lock:
            tasks = [session.close() for session in tuple(self._sessions.values())]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result


class EngineSession:
    def __init__(
        self,
        agent: EngineAgent,
        runtime: Runtime,
        info: SessionRecord,
        model: str,
        *,
        lease: SessionLease | None = None,
        reasoning_effort: str | None = None,
        config: ResolvedConfig | None = None,
    ) -> None:
        self.agent, self.runtime, self._info, self.model = agent, runtime, info, model
        # The provider connection the session's selection names.
        self.config = config or agent.config
        # The reasoning effort named for the session, or None when the default applies.
        self.reasoning_effort = reasoning_effort
        self._history: list[TurnRecord] = []
        self._committed: list[CommittedTurn] = []
        self._accepted = False
        self._inherited = False
        self._continuation = "fresh"
        self._lease = lease
        self._fault: AgentError | None = None
        self._admission = asyncio.Lock()
        self._active: EngineTurn | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def _check(self) -> None:
        if self._closed or self.agent._closed:
            raise closed()

    @property
    def info(self) -> SessionRecord:
        self._check()
        return SessionRecord(
            self._info.session_id,
            self._info.persistence,
            self.config.provider,
            self.model,
            self.reasoning_effort or reasoning.DEFAULT,
        )

    @property
    def history(self) -> list[TurnRecord]:
        self._check()
        return copy.deepcopy(self._history)

    async def start_turn(self, input: TurnInput) -> EngineTurn:
        self._check()
        if self._active is not None or self._admission.locked():
            raise busy("Wait for the active turn's terminal event.")
        if self._fault is not None:
            raise copy.deepcopy(self._fault)
        value = turn_input(
            input,
            seed_allowed=self._info.persistence == "ephemeral" and not self._accepted and not self._inherited,
        )
        config = self.config
        model = named(value.model, self.model)
        effort_named = value.reasoning_effort or self.reasoning_effort
        effort = reasoning.sent(config.provider, model, effort_named, getattr(self.runtime, "provider", None))
        if config.provider == "github-copilot" and copilot_drops_images(value, self._history):
            raise copilot_unsupported(model)
        if holds_image([value, *conversation_inputs(self._history)]):
            # Admission stays held across the lookup, so a concurrent start sees busy.
            async with self._admission:
                capabilities = await self.agent.model_capabilities(self.runtime, config.provider, model)
            self._check()
            if self._fault is not None:
                raise copy.deepcopy(self._fault)
            if capabilities is not None and "vision" not in capabilities:
                raise image_unsupported(config.provider, model)
        turn = EngineTurn(self, value, model)
        turn.reasoning_effort, turn.reasoning_ceiling = effort, effort_named or reasoning.DEFAULT
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
        if self._active is not None or self._admission.locked():
            raise busy("Wait for the active turn's terminal event before forking.")
        if self._fault is not None:
            raise copy.deepcopy(self._fault)
        async with self._admission:
            messages = await self._messages()
        branch = Branch(
            self._info.session_id,
            messages,
            copy.deepcopy(self._history),
            copy.deepcopy(self._committed),
            self._accepted or self._inherited,
        )
        async with self.agent._lock:
            self._check()
            return await self.agent._create(
                str(uuid.uuid4()),
                self._info.persistence,
                self.config,
                self.model,
                branch,
                reasoning_effort=self.reasoning_effort,
            )

    async def set_model(self, provider: str, model: str, reasoning_effort: str | None = None) -> None:
        """Replace the session's selection between turns; on failure nothing changes."""
        self._check()
        if self._active is not None or self._admission.locked():
            raise busy("Wait for the active turn's terminal event, then select the model again.")
        if self._fault is not None:
            raise copy.deepcopy(self._fault)
        selection.check(provider, model)
        effort = reasoning.parse(reasoning_effort, "reasoning_effort")
        if effort is None:
            effort = self.reasoning_effort
        async with self._admission:
            config = selection.configure(self.agent.config, provider, model)
            instance = await self.runtime.connect(config)
            try:
                self._check()
                reasoning.check(provider, model, effort, instance)
                messages = None
                if provider != self.config.provider:
                    messages = selection.without_reasoning(await self._messages())
                if self._lease is not None:
                    self.agent._store.select(self._info.session_id, selected(config, model, effort), messages)
            except BaseException:
                await self.runtime.discard(instance)
                raise
            await self.runtime.switch(config, instance, messages)
            self.config, self.model, self.reasoning_effort = config, model, effort

    async def _messages(self) -> list[dict[str, Any]]:
        try:
            snapshot = await self.runtime.snapshot()
        except AgentError:
            raise
        except Exception as exc:
            raise storage_error() from exc
        return copy.deepcopy(snapshot["messages"])

    async def _record(self, turn: EngineTurn, result: TurnResult) -> None:
        record = TurnRecord(turn._info.turn_id, copy.deepcopy(turn.input), copy.deepcopy(result))
        history = [*self._history, record]
        if self._lease is not None:
            try:
                messages = await self._messages()
                if turn.cancelled:
                    result.state = "cancelled"
                    result.error = turn._cancel_error()
                    record.result = copy.deepcopy(result)
                committed = [*self._committed, CommittedTurn(copy.deepcopy(record), len(messages))]
                facts = {
                    **selected(self.config, self.model, self.reasoning_effort),
                    "working_dir": str(self.agent.config.working_directory),
                }
                self.agent._store.commit(self._info.session_id, messages, committed, facts)
                self._committed = committed
            except Exception as exc:
                error = exc if isinstance(exc, AgentError) else storage_error()
                self._fault = error
                if turn.cancelled:
                    result.state = "cancelled"
                    result.error = turn._cancel_error()
                    result.error.details = {
                        **(result.error.details or {}),
                        "persistence_error": copy.deepcopy(vars(error)),
                    }
                else:
                    result.state = "failure"
                    result.error = error
                record.result = copy.deepcopy(result)
        self._history = history
        self._continuation = "resumed"

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await settled(self._close_task)

    async def _close(self) -> None:
        try:
            if self._active is not None:
                await self._active.cancel()
            async with self._admission:
                await self.runtime.close()
        finally:
            if self._lease is not None:
                self._lease.close()
                self._lease = None
            self.agent._sessions.pop(self._info.session_id, None)


class EngineTurn:
    def __init__(self, session: EngineSession, input: TurnInput, model: str) -> None:
        self.session, self.input, self.model = session, input, model
        # What primary work sends, and the most any work in the turn may run at.
        self.reasoning_effort: str | None = None
        self.reasoning_ceiling = reasoning.DEFAULT
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
        self.recovery = RecoveryState()
        self.effect_call_ids: set[str] = set()
        # The parts of completed results holding images, by call id; events carry only their text.
        self.tool_parts: dict[str, list[ContentPart]] = {}
        self._continuation = session._continuation

    @property
    def info(self) -> TurnInfo:
        self.session._check()
        return self._info

    @property
    def final_history(self) -> list[TurnRecord]:
        return copy.deepcopy(self._final_history)

    @property
    def inspection_only(self) -> bool:
        return self.recovery.uncertain_call is not None

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

    def progress(self, data: Any) -> None:
        self.emit("progress", Progress(copy.deepcopy(data)))

    def reasoning(self, text: str, *, final: bool = False) -> None:
        if final:
            self.emit("reasoning_final", ReasoningFinal("".join(self._reasoning)))
            self._reasoning.clear()
        else:
            self._reasoning.append(text)
            self.emit("reasoning_delta", ReasoningDelta(text))

    def work_started(self, provider: str | None = None, model: str | None = None) -> None:
        key = (provider or self.session.agent.config.provider, model or self.model)
        self._usage.setdefault(key, UsageEntry(*key))
        self._requests[key] = self._requests.get(key, 0) + 1
        self.emit("usage", UsageEvent(self.usage_snapshot()))

    def work_resolved(self, provider: str, requested: str, actual: str) -> None:
        if requested == actual:
            return
        old, new = (provider, requested), (provider, actual)
        self._requests[old] -= 1
        if self._requests[old] == 0:
            self._usage.pop(old, None)
        self._requests[new] = self._requests.get(new, 0) + 1
        self._usage.setdefault(new, UsageEntry(*new))

    def usage_snapshot(self) -> Usage:
        entries = copy.deepcopy(list(self._usage.values()))
        for entry in entries:
            key = (entry.provider, entry.model)
            for name in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"):
                if self._known.get((*key, name), 0) != self._requests.get(key, 0):
                    setattr(entry, name, None)
            if entry.cost is not None:
                entry.cost = {
                    currency: value
                    for currency, value in entry.cost.items()
                    if self._known.get((*key, f"cost.{currency}"), 0) == self._requests.get(key, 0)
                } or None
        return Usage(entries)

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
        self.emit("usage", UsageEvent(self.usage_snapshot()))

    def stop(self, state: str, error: AgentError) -> PolicyStop:
        if self._policy is None:
            self._policy = TurnResult(cast(Any, state), error=error)
        return PolicyStop()

    def fail(self, error: Exception) -> None:
        if isinstance(error, AgentError):
            self.stop("failure", error)

    async def call_tool(self, tool: Any, call_id: str, arguments: dict[str, Any]) -> ToolResolution:
        return await execute_tool(self, self.session.agent.config, tool, call_id, arguments)

    async def _execute(self) -> None:
        self._entered.set()
        self.emit(
            "turn_started",
            TurnStarted(
                cast(Any, self._continuation),
                Selection(self.session.config.provider, self.model),
                self.reasoning_effort,
            ),
        )
        result = TurnResult("success")
        context_token = active_turn_id.set(self.info.turn_id)
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
            try:
                await settled(asyncio.create_task(self._finish(result)))
            finally:
                active_turn_id.reset(context_token)

    async def _finish(self, result: TurnResult) -> None:
        remaining = [task for task in self.pending | self.recovery.executing.keys() if not task.done()]
        for task in remaining:
            if self.cancelled or self.recovery.uncertain_call is None or task not in self.recovery.executing:
                task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        try:
            await self.session.runtime.settle(self._resolutions)
            error = self._policy.error if self._policy is not None else None
            if not self.cancelled and error is not None and error.code == "image_unsupported":
                await self.session.runtime.describe_images(self._resolutions)
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
            result.usage = self.usage_snapshot()
            self.emit("usage", UsageEvent(result.usage))
        await self.session._record(self, result)
        self._final_history = list(self.session._history)
        self.session._active = None
        self.emit("terminal", result)
        self._result = result

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
        if self._task.done() or self._result is not None:
            return
        if self._cancel_task is None:
            self.cancelled = True
            self.session.runtime.request_cancel()
            self._cancel_task = asyncio.create_task(self._cancel())
        await settled(self._cancel_task)

    async def _cancel(self) -> None:
        await self._entered.wait()
        remaining = [task for task in self.pending | self.recovery.executing.keys() if not task.done()]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
