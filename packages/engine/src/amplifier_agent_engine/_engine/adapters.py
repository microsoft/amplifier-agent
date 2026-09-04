"""Translate Amplifier execution into owned records and task lifetimes."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_module_context_simple import SimpleContextManager

from .._records import AgentError, Tool, ToolResolution, TurnInput, UsageEntry
from .configuration import ResolvedConfig, strict_json
from .effects import PolicyStop
from .ports import Observer


class ProviderHooks:
    def __init__(self, runtime: AmplifierRuntime, hooks: Any) -> None:
        self._runtime, self._hooks = runtime, hooks

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hooks, name)

    async def emit(self, name: str, data: dict[str, Any]) -> Any:
        if re.fullmatch(r"(?:[a-z][a-z0-9-]*\.)+[a-z][a-z0-9_-]*", name):
            observer = self._runtime.observer
            if observer is not None:
                strict_json(data, name)
                fields = {key: value for key, value in data.items() if "." in key}
                observer.extension(name, data.get("payload", data), fields)
        return await self._hooks.emit(name, data)


class ProviderCoordinator:
    def __init__(self, runtime: AmplifierRuntime) -> None:
        self._coordinator = runtime.core.coordinator
        self.hooks = ProviderHooks(runtime, self._coordinator.hooks)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._coordinator, name)


class StructuredContext(SimpleContextManager):
    """Replace the loop's one explicit turn-entry append with structured messages."""

    def __init__(self) -> None:
        super().__init__()
        self.entry_token: str | None = None
        self.entry_messages: list[dict[str, Any]] = []

    def prepare(self, input: TurnInput) -> str:
        self.entry_token = str(uuid.uuid4())
        self.entry_messages = [asdict(message) for message in input.history or []]
        if input.content:
            self.entry_messages.append(
                {"role": "user", "content": [asdict(part) for part in input.content]}
            )
        return self.entry_token

    async def add_message(self, message: dict[str, Any]) -> None:
        if (
            self.entry_token is not None
            and message.get("role") == "user"
            and message.get("content") == self.entry_token
        ):
            self.entry_token = None
            for item in self.entry_messages:
                await super().add_message(item)
            self.entry_messages = []
            return
        await super().add_message(message)

    async def get_messages_for_request(self, **kwargs: Any) -> list[dict[str, Any]]:
        # Conversation replay is complete; admission never silently compacts input.
        return copy.deepcopy(self._strip_internal_metadata(await self.get_messages()))


class ProviderAdapter:
    priority = 100

    def __init__(self, runtime: AmplifierRuntime, provider: Any) -> None:
        self.runtime, self.provider = runtime, provider
        self.name = runtime.config.provider
        self.config = {"default_model": runtime.config.model}

    def get_info(self) -> Any:
        info = self.provider.get_info()
        defaults = dict(info.defaults)
        defaults["model"] = (
            self.runtime.observer.model if self.runtime.observer else self.runtime.config.model
        )
        return SimpleNamespace(defaults=defaults, capabilities=info.capabilities)

    def parse_tool_calls(self, response: Any) -> Any:
        return self.provider.parse_tool_calls(response)

    async def complete(self, request: Any, **kwargs: Any) -> Any:
        observer = self.runtime.require_observer()
        if observer.cancelled:
            raise asyncio.CancelledError
        task = asyncio.current_task()
        assert task is not None
        observer.pending.add(task)
        observer.work_started()
        self.runtime.response_chunks = []
        try:
            kwargs["model"] = observer.model
            response = await self.provider.complete(request, **kwargs)
            for call in self.provider.parse_tool_calls(response):
                if call.name not in {tool.name for tool in self.runtime.config.tools}:
                    raise AgentError(
                        "provider_failed",
                        "provider",
                        "The provider requested an undeclared tool.",
                        "Configure the requested tool or correct the provider response.",
                        details={"tool": call.name},
                    )
                if not isinstance(call.arguments, dict):
                    raise AgentError(
                        "provider_failed",
                        "provider",
                        "The provider supplied invalid tool arguments.",
                        "Use a provider that supplies decoded JSON objects for tool arguments.",
                    )
                strict_json(call.arguments, "tool.arguments")
            usage = response.usage
            if usage is not None:
                cost = getattr(usage, "cost_usd", None)
                observer.usage(
                    UsageEntry(
                        self.name,
                        observer.model,
                        tokens_in=getattr(usage, "input_tokens", None),
                        tokens_out=getattr(usage, "output_tokens", None),
                        cache_read_tokens=getattr(usage, "cache_read_tokens", None),
                        cache_write_tokens=getattr(usage, "cache_write_tokens", None),
                        cost={"USD": cost if isinstance(cost, Decimal) else Decimal(str(cost))}
                        if cost is not None
                        else None,
                    )
                )
            text = "".join(
                block.text
                for block in response.content or []
                if getattr(block, "type", None) == "text"
            )
            streamed = "".join(self.runtime.response_chunks)
            if not streamed and text:
                observer.output(text)
            elif streamed != text:
                error = AgentError(
                    "internal_failed",
                    "internal",
                    "The provider's final text disagrees with its live output.",
                    "Retry with a provider adapter that preserves streamed text.",
                )
                observer.fail(error)
                raise PolicyStop()
            return response
        except AgentError as exc:
            observer.fail(exc)
            raise PolicyStop() from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = AgentError(
                "provider_failed",
                "provider",
                "The provider request failed.",
                "Check provider credentials, availability, and request compatibility before starting another turn.",
                retryable=bool(getattr(exc, "retryable", False)),
                details={"provider": self.name, "model": observer.model},
            )
            observer.fail(error)
            raise PolicyStop() from exc
        finally:
            observer.pending.discard(task)


class CallerToolAdapter:
    def __init__(self, runtime: AmplifierRuntime, tool: Tool) -> None:
        self.runtime, self.tool = runtime, tool
        self.name, self.description, self.input_schema = (
            tool.name,
            tool.description,
            tool.input_schema,
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        observer = self.runtime.require_observer()
        if observer.cancelled:
            raise asyncio.CancelledError
        task = asyncio.current_task()
        assert task is not None
        observer.pending.add(task)
        try:
            contexts = getattr(self.runtime.core.coordinator, "_tool_dispatch_contexts", {})
            dispatch = contexts.get(task, {})
            call_id = dispatch.get("tool_call_id")
            if not isinstance(call_id, str):
                error = AgentError(
                    "internal_failed",
                    "internal",
                    "The tool request has no correlation id.",
                    "Install compatible execution dependencies before retrying.",
                )
                observer.fail(error)
                raise PolicyStop()
            content = await observer.call_tool(self.tool, call_id, arguments)
            return ToolResult(success=True, output=content)
        except AgentError as exc:
            observer.fail(exc)
            raise PolicyStop() from exc
        finally:
            observer.pending.discard(task)


class AmplifierRuntime:
    def __init__(self, config: ResolvedConfig) -> None:
        self.config = config
        self.observer: Observer | None = None
        self.response_chunks: list[str] = []
        self.context = StructuredContext()
        self.provider: Any = None
        self.core = AmplifierSession(
            {
                "session": {
                    "orchestrator": {
                        "module": "loop-streaming",
                        "config": {
                            "max_iterations": -1,
                            "min_delay_between_calls_ms": 0,
                            "stream_delay": 0,
                        },
                    },
                    "context": {"module": "context-simple"},
                }
            }
        )
        self._closed = False
        self._turn_start = 0

    async def initialize(self, provider_factory: Any) -> None:
        try:
            await self.core.initialize()
            await self.core.coordinator.mount("context", self.context)
            if self.config.instructions is not None:
                await self.context.add_message(
                    {"role": "system", "content": self.config.instructions}
                )
            self.provider = await provider_factory(self.config, ProviderCoordinator(self))
            await self.core.coordinator.mount(
                "providers", ProviderAdapter(self, self.provider), name=self.config.provider
            )
            for tool in self.config.tools:
                await self.core.coordinator.mount(
                    "tools", CallerToolAdapter(self, tool), name=tool.name
                )
            self.core.coordinator.hooks.register(
                "llm:stream_block_delta", self._delta, name="agent-output"
            )
            self.core.coordinator.hooks.register(
                "llm:stream_block_end", self._block_stop, name="agent-reasoning"
            )
        except BaseException:
            await self.close()
            raise

    async def _delta(self, event: str, data: dict[str, Any]) -> HookResult:
        observer = self.observer
        if observer is not None:
            if data.get("block_type") == "text" and isinstance(data.get("text"), str):
                self.response_chunks.append(data["text"])
                observer.output(data["text"])
            elif data.get("block_type") == "thinking" and isinstance(data.get("text"), str):
                observer.reasoning(data["text"])
        return HookResult()

    async def _block_stop(self, event: str, data: dict[str, Any]) -> HookResult:
        if self.observer is not None and data.get("block_type") == "thinking":
            self.observer.reasoning("", final=True)
        return HookResult()

    def require_observer(self) -> Observer:
        if self.observer is None:
            raise RuntimeError("No active turn owns this execution")
        return self.observer

    async def execute(self, input: TurnInput, observer: Observer) -> None:
        self.observer = observer
        self.core.coordinator.cancellation.reset()
        self._turn_start = len(await self.context.get_messages())
        entry = self.context.prepare(input)
        try:
            await self.core.execute(entry)
            if self.context.entry_token is not None:
                raise RuntimeError("The execution did not consume its structured entry")
        finally:
            self.observer = None

    def request_cancel(self) -> None:
        self.core.coordinator.cancellation.request_immediate()

    async def settle(self, resolutions: list[ToolResolution]) -> None:
        messages = await self.context.get_messages()
        calls = {
            call["id"]: call["tool"]
            for message in messages[self._turn_start :]
            for call in message.get("tool_calls", [])
        }
        known = {resolution.call_id: resolution for resolution in resolutions}
        existing = {
            message.get("tool_call_id")
            for message in messages[self._turn_start :]
            if message["role"] == "tool"
        }
        for call_id, name in calls.items():
            resolution = known.get(call_id)
            if resolution is None and call_id in existing:
                continue
            resolution = resolution or ToolResolution(call_id, "cancelled")
            content = resolution.content
            if resolution.outcome != "completed":
                content = json.dumps(
                    {
                        "outcome": resolution.outcome,
                        "error": resolution.error.message if resolution.error else None,
                    }
                )
            if call_id in existing:
                for message in messages[self._turn_start :]:
                    if message.get("tool_call_id") == call_id:
                        message["content"] = content or ""
            else:
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "tool_call_id": call_id,
                        "content": content or "",
                    }
                )
        if calls:
            await self.context.set_messages(messages)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.core.cleanup()
        finally:
            if self.provider is not None and callable(getattr(self.provider, "close", None)):
                await self.provider.close()
