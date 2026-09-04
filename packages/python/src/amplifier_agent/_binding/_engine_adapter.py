"""Translate the owned engine's values into the Python binding's values."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import is_dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .. import _records as public

if TYPE_CHECKING:
    from amplifier_agent_engine._ports import AgentPort as EngineAgentPort
    from amplifier_agent_engine._ports import SessionPort as EngineSessionPort
    from amplifier_agent_engine._ports import TurnPort as EngineTurnPort

T = TypeVar("T")

# Registered records and fields have explicit conversions at this boundary.
_FIELDS = (
    ("TextPart", ("text", "type")),
    ("ConversationMessage", ("role", "content")),
    ("TurnInput", ("content", "model", "history")),
    (
        "UsageEntry",
        (
            "provider",
            "model",
            "tokens_in",
            "tokens_out",
            "cache_read_tokens",
            "cache_write_tokens",
            "cost",
        ),
    ),
    ("Usage", ("entries",)),
    ("TurnResult", ("state", "content", "error", "usage")),
    ("SessionRecord", ("session_id", "persistence")),
    ("TurnInfo", ("session_id", "turn_id")),
    ("TurnRecord", ("turn_id", "input", "result")),
    ("Event", ("contract_version", "session_id", "turn_id", "sequence", "type", "payload", "at")),
    ("ToolContext", ("call_id", "deadline")),
    ("Tool", ("name", "description", "input_schema", "handler", "safety")),
    ("ToolCall", ("call_id", "name", "source", "arguments", "deadline")),
    ("ToolResolution", ("call_id", "outcome", "content", "error")),
    ("McpServer", ("name", "transport", "command", "args", "env", "url", "headers")),
    ("ApprovalRequest", ("request_id", "summary", "call_id", "name")),
    ("ApprovalResponse", ("decision", "reason")),
    (
        "AgentOptions",
        (
            "provider",
            "model",
            "instructions",
            "tools",
            "skills",
            "mcp_servers",
            "storage",
            "approvals",
        ),
    ),
    ("SessionOptions", ("session_id", "persistence", "model")),
    ("Selection", ("provider", "model")),
    ("TurnStarted", ("continuation", "primary_actual")),
    ("OutputDelta", ("content",)),
    ("ReasoningDelta", ("text",)),
    ("ReasoningFinal", ("text",)),
    ("ToolCallEvent", ("call",)),
    ("ToolResultEvent", ("resolution",)),
    ("ApprovalRequestEvent", ("request",)),
    ("ApprovalResolution", ("request_id", "decision", "reason")),
    ("ApprovalDecision", ("resolution",)),
    ("Progress", ("data",)),
    ("UsageEvent", ("snapshot",)),
)
_ERROR_FIELDS = ("code", "category", "message", "remedy", "retryable", "correlation_id", "details")


class RecordBridge:
    def __init__(self, engine_records: ModuleType) -> None:
        self._engine = engine_records
        self.engine_error: type[Exception] = engine_records.AgentError
        self._inputs = {
            getattr(public, name): (getattr(engine_records, name), names) for name, names in _FIELDS
        }
        self._outputs = {
            getattr(engine_records, name): (getattr(public, name), names) for name, names in _FIELDS
        }

    def to_engine(self, value: Any) -> Any:
        return self._convert(value, into_engine=True)

    def to_public(self, value: Any) -> Any:
        return self._convert(value, into_engine=False)

    def _convert(self, value: Any, *, into_engine: bool) -> Any:
        if value is None or isinstance(value, (str, int, float, bool, Decimal, datetime, Path)):
            return value
        convert = self.to_engine if into_engine else self.to_public
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, tuple):
            return tuple(convert(item) for item in value)
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        source_error = public.AgentError if into_engine else self.engine_error
        if isinstance(value, source_error):
            target = self.engine_error if into_engine else public.AgentError
            result = target(**{name: convert(getattr(value, name)) for name in _ERROR_FIELDS})
            self._additional_fields(value, result, _ERROR_FIELDS, convert)
            return result
        mappings = self._inputs if into_engine else self._outputs
        mapping = mappings.get(type(value))
        if mapping is None:
            mapping = next(
                (target for source, target in mappings.items() if isinstance(value, source)), None
            )
        if mapping is not None:
            record_type, names = mapping
            arguments = {name: convert(getattr(value, name)) for name in names}
            if into_engine and isinstance(value, public.Tool) and callable(value.handler):
                arguments["handler"] = self._tool_handler(value.handler)
            if into_engine and isinstance(value, public.AgentOptions) and callable(value.approvals):
                arguments["approvals"] = self._approval_handler(value.approvals)
            result = record_type(**arguments)
            self._additional_fields(value, result, names, convert)
            return result
        if into_engine:
            # Invalid inputs remain available to the engine's named validation.
            return value
        if is_dataclass(value) and not isinstance(value, type):
            return {name: convert(item) for name, item in vars(value).items()}
        raise public.AgentError(
            "internal_failed",
            "internal",
            "An execution value could not be presented by the library.",
            "Install matching library dependencies before retrying.",
        )

    @staticmethod
    def _additional_fields(
        source: Any, target: Any, names: tuple[str, ...], convert: Callable[[Any], Any]
    ) -> None:
        for name, value in vars(source).items():
            if name not in names:
                object.__setattr__(target, name, convert(value))

    def _tool_handler(self, handler: public.ToolHandler) -> Callable[..., Awaitable[str]]:
        async def invoke(arguments: dict[str, Any], context: Any) -> str:
            try:
                return await handler(self.to_public(arguments), self.to_public(context))
            except public.ToolFailed as exc:
                raise self._engine.ToolFailed(str(exc)) from None
            except public.ToolOutcomeUnknown as exc:
                raise self._engine.ToolOutcomeUnknown(str(exc)) from None

        return invoke

    def _approval_handler(self, handler: public.ApprovalHandler) -> Callable[..., Awaitable[Any]]:
        async def invoke(request: Any) -> Any:
            return self.to_engine(await handler(self.to_public(request)))

        return invoke

    def read(self, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except self.engine_error as exc:
            raise self.to_public(exc) from None

    async def call(self, operation: Callable[..., Awaitable[T]], *args: Any) -> T:
        try:
            return await operation(*args)
        except self.engine_error as exc:
            raise self.to_public(exc) from None


class AgentAdapter:
    def __init__(self, target: EngineAgentPort, bridge: RecordBridge) -> None:
        self._target, self._bridge = target, bridge

    @property
    def contract_versions(self) -> tuple[str, ...]:
        return self._bridge.read(lambda: tuple(self._target.contract_versions))

    async def create_session(self, options: public.SessionOptions | None = None) -> SessionAdapter:
        target = await self._bridge.call(
            self._target.create_session, self._bridge.to_engine(options)
        )
        return SessionAdapter(target, self._bridge)

    async def resume_session(self, session_id: str) -> SessionAdapter:
        target = await self._bridge.call(self._target.resume_session, session_id)
        return SessionAdapter(target, self._bridge)

    async def list_sessions(self) -> list[public.SessionRecord]:
        return self._bridge.to_public(await self._bridge.call(self._target.list_sessions))

    async def delete_session(self, session_id: str) -> None:
        await self._bridge.call(self._target.delete_session, session_id)

    async def close(self) -> None:
        await self._bridge.call(self._target.close)


class SessionAdapter:
    def __init__(self, target: EngineSessionPort, bridge: RecordBridge) -> None:
        self._target, self._bridge = target, bridge

    @property
    def info(self) -> public.SessionRecord:
        return self._bridge.to_public(self._bridge.read(lambda: self._target.info))

    @property
    def history(self) -> list[public.TurnRecord]:
        return self._bridge.to_public(self._bridge.read(lambda: self._target.history))

    async def run(self, input: public.TurnInput) -> public.TurnResult:
        result = await self._bridge.call(self._target.run, self._bridge.to_engine(input))
        return self._bridge.to_public(result)

    async def run_with_history(
        self, input: public.TurnInput
    ) -> tuple[public.TurnResult, list[public.TurnRecord]]:
        result = await self._bridge.call(
            self._target.run_with_history, self._bridge.to_engine(input)
        )
        return self._bridge.to_public(result)

    async def start_turn(self, input: public.TurnInput) -> TurnAdapter:
        target = await self._bridge.call(self._target.start_turn, self._bridge.to_engine(input))
        return TurnAdapter(target, self._bridge)

    async def fork(self) -> SessionAdapter:
        return SessionAdapter(await self._bridge.call(self._target.fork), self._bridge)

    async def close(self) -> None:
        await self._bridge.call(self._target.close)


class TurnAdapter:
    def __init__(self, target: EngineTurnPort, bridge: RecordBridge) -> None:
        self._target, self._bridge = target, bridge

    @property
    def info(self) -> public.TurnInfo:
        return self._bridge.to_public(self._bridge.read(lambda: self._target.info))

    @property
    def final_history(self) -> list[public.TurnRecord]:
        return self._bridge.to_public(self._bridge.read(lambda: self._target.final_history))

    def events(self) -> AsyncIterator[public.Event]:
        return self._events(self._bridge.read(self._target.events))

    async def _events(self, source: AsyncIterator[Any]) -> AsyncIterator[public.Event]:
        try:
            async for event in source:
                yield cast(public.Event, self._bridge.to_public(event))
        except self._bridge.engine_error as exc:
            raise self._bridge.to_public(exc) from None
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()

    async def cancel(self) -> None:
        await self._bridge.call(self._target.cancel)
