"""Translate Amplifier execution into owned records and task lifetimes."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
import uuid
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_module_context_simple import SimpleContextManager

from .._records import AgentError, TextPart, ToolResolution, TurnInput, UsageEntry
from .configuration import ResolvedConfig, strict_json
from .effects import PolicyStop, resolution_text
from .ports import Observer
from .provider_policy import response_selection, response_usage
from .skill_hooks import HookScope, SkillHooks


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
    """Replace the loop's one explicit turn-entry append with structured messages.

    The engine bounds a completed tool result before emitting it, so conversation
    ingress admits every result the engine kept, plus the envelope an unsuccessful
    resolution carries around its partial output.
    """

    ENVELOPE_BYTES = 4096

    def __init__(self, tool_result_max_bytes: int | None) -> None:
        super().__init__(max_tool_result_bytes=(
            sys.maxsize if tool_result_max_bytes is None
            else tool_result_max_bytes + self.ENVELOPE_BYTES
        ))
        self.entry_token: str | None = None
        self.entry_messages: list[dict[str, Any]] = []
        self.turn_active = False
        self.assistant_allowance = 0
        self.skill_context: list[str] = []

    def prepare(self, input: TurnInput) -> str:
        self.entry_token = str(uuid.uuid4())
        self.turn_active = True
        self.assistant_allowance = 0
        self.entry_messages = [asdict(message) for message in input.history or []]
        if input.content:
            self.entry_messages.append(
                {"role": "user", "content": [asdict(part) for part in input.content]}
            )
        return self.entry_token

    async def add_message(self, message: dict[str, Any]) -> None:
        if self.turn_active and message.get("role") == "assistant":
            if self.assistant_allowance == 0:
                return
            self.assistant_allowance -= 1
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

    async def get_messages_for_request(
        self, token_budget: int | None = None, provider: Any | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        # Conversation replay is complete; admission never silently compacts input.
        messages = copy.deepcopy(self._strip_internal_metadata(await self.get_messages()))
        if self.skill_context:
            messages.append({"role": "user", "content": "\n\n".join(self.skill_context)})
        return messages


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
        observer.work_started(self.name, observer.model)
        self.runtime.response_chunks = []
        self.runtime.response_pending = True
        try:
            kwargs["model"] = observer.model
            available = self.runtime.skill_hooks.tools()
            if request.tools:
                offered = available
                if observer.inspection_only:
                    offered = {name for name in available
                               if self.runtime.registry.tools[name].read_only_inspection
                               and not self.runtime.registry.tools[name].guard}
                request = request.model_copy(update={
                    "tools": [tool for tool in request.tools if tool.name in offered],
                })
            response = await self.provider.complete(request, **kwargs)
            actual = getattr(response, "agent_actual_model", None) or observer.model
            observer.work_resolved(self.name, observer.model, actual)
            usage = response_usage(response, self.name, actual)
            if usage is not None:
                observer.usage(usage)
            response_selection(response, observer.model)
            for call in self.provider.parse_tool_calls(response):
                if not isinstance(call.id, str) or not call.id or call.id in self.runtime._call_ids:
                    raise AgentError(
                        "provider_failed", "provider", "The provider supplied a repeated or invalid tool call id.",
                        "Use a provider that identifies each tool request uniquely within the turn.",
                    )
                self.runtime._call_ids.add(call.id)
                if call.name not in available:
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
            self.runtime.context.assistant_allowance += 1
            self.runtime.response_pending = False
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
    def __init__(self, runtime: AmplifierRuntime, tool: Any) -> None:
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
            call_id = self.runtime.correlate(call_id)
            resolution = await self.runtime.call_tool(self.tool, call_id, arguments)
            return ToolResult(
                success=resolution.outcome == "completed",
                output=resolution.content if resolution.outcome == "completed"
                else json.loads(resolution_text(resolution)),
                error=vars(resolution.error) if resolution.error else None,
            )
        except AgentError as exc:
            observer.fail(exc)
            raise PolicyStop() from exc
        finally:
            observer.pending.discard(task)


class DelegatedObserver:
    def __init__(self, parent: Observer, model: str) -> None:
        self.parent, self.model = parent, model
        self.parts: list[str] = []
        self.error: Exception | None = None

    @property
    def cancelled(self) -> bool:
        return self.parent.cancelled

    @property
    def pending(self) -> set[asyncio.Task[Any]]:
        return self.parent.pending

    @property
    def inspection_only(self) -> bool:
        return self.parent.inspection_only

    def output(self, text: str) -> None:
        self.parts.append(text)

    def reasoning(self, text: str, *, final: bool = False) -> None:
        self.parent.reasoning(text, final=final)

    def work_started(self, provider: str | None = None, model: str | None = None) -> None:
        self.parent.work_started(provider, model or self.model)

    def work_resolved(self, provider: str, requested: str, actual: str) -> None:
        self.parent.work_resolved(provider, requested, actual)

    def usage(self, entry: UsageEntry) -> None:
        self.parent.usage(entry)

    def extension(self, name: str, payload: Any, fields: dict[str, Any]) -> None:
        self.parent.extension(name, payload, fields)

    def fail(self, error: Exception) -> None:
        self.error = error
        self.parent.fail(error)

    async def call_tool(self, tool: Any, call_id: str, arguments: dict[str, Any]) -> ToolResolution:
        return await self.parent.call_tool(tool, call_id, arguments)


def observation_hooks(config: ResolvedConfig) -> list[dict[str, Any]]:
    """Redaction runs at priority 10, ahead of the capture at 100, so the capture holds
    redacted payloads. The capture hook warns whenever its root differs from the CLI's
    environment-selected root, which is always the case here, so only its errors are
    logged; forwarding trouble is still recorded under ``context-intelligence-logs``."""
    return [
        {"module": "hook-redaction", "config": {}},
        {
            "module": "hook-context-intelligence",
            "config": {
                "base_path": str(config.storage / "workspaces"),
                "project_slug": config.workspace,
                "workspace": config.workspace,
                "forwarding_log_dir": str(config.storage / "context-intelligence-logs"),
                "destinations": copy.deepcopy(config.context_intelligence),
                "close_drain_timeout": 2.0,
                "log_level": "ERROR",
            },
        },
    ]


class AmplifierRuntime:
    def __init__(
        self,
        config: ResolvedConfig,
        *,
        session_id: str,
        parent_id: str | None = None,
        resumed: bool = False,
        capture: bool = True,
    ) -> None:
        self.config = config
        self.session_id = session_id
        self.capture = capture
        self.observer: Observer | None = None
        self.response_chunks: list[str] = []
        self.response_pending = False
        self.context = StructuredContext(config.tool_result_max_bytes)
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
                },
                "hooks": observation_hooks(config) if capture else [],
            },
            session_id=session_id,
            parent_id=parent_id,
            is_resumed=resumed,
        )
        self._closed = False
        self._turn_start = 0
        self._instruction_count = 0
        self.registry: Any = None
        self.parent_registry: Any = None
        self.allowed_tools: tuple[str, ...] | None = None
        self._provider_factory: Any = None
        self._children: set[AmplifierRuntime] = set()
        self._delegate_lock = asyncio.Lock()
        self._call_prefix = ""
        self._call_ids: set[str] = set()
        self.skill_hooks = HookScope(self)
        self.inherited_skill_hooks: tuple[SkillHooks, ...] = ()
        self.skill_fork = False
        self.allowed_skill_agents: tuple[str, ...] | None = None

    async def initialize(self, provider_factory: Any) -> None:
        from .tools import prepare_tools

        self._provider_factory = provider_factory
        try:
            self.core.coordinator.register_capability(
                "session.working_dir", str(self.config.working_directory)
            )
            await self.core.initialize()
            await self.core.coordinator.mount("context", self.context)
            register = getattr(self.core.coordinator, "register_capability", None)
            if callable(register):
                register(
                    "context.request_retention",
                    self.context.get_messages_for_request_retaining,
                )
                register("context.foreground_usage", self.context.claim_foreground_usage)
            if self.config.instructions is not None:
                await self.context.add_message(
                    {"role": "system", "content": self.config.instructions}
                )
                self._instruction_count = 1
            self.provider = await provider_factory(self.config, ProviderCoordinator(self))
            await self.core.coordinator.mount(
                "providers", ProviderAdapter(self, self.provider), name=self.config.provider
            )
            self.registry = await prepare_tools(self)
            self.skill_hooks.validate_tools()
            for tool in self.registry.tools.values():
                await self.core.coordinator.mount(
                    "tools", CallerToolAdapter(self, tool), name=tool.name
                )
            self.core.coordinator.hooks.register(
                "llm:stream_block_delta", self._delta, name="agent-output"
            )
            self.core.coordinator.hooks.register(
                "llm:stream_block_end", self._block_stop, name="agent-reasoning"
            )
            self.core.coordinator.hooks.register(
                "tool:post", self._tool_result_boundary, priority=200, name="agent-tool-result"
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

    async def _tool_result_boundary(self, event: str, data: dict[str, Any]) -> HookResult:
        """Observation hooks run first and see a redacted copy of the tool result; the loop
        treats any replaced ``result`` as the text the model reads. Clearing it after the
        observers restores the loop's own serialization of the actual result."""
        return HookResult(action="modify", data={**data, "result": None})

    async def _block_stop(self, event: str, data: dict[str, Any]) -> HookResult:
        if self.observer is not None and data.get("block_type") == "thinking":
            self.observer.reasoning("", final=True)
        return HookResult()

    def require_observer(self) -> Observer:
        if self.observer is None:
            raise RuntimeError("No active turn owns this execution")
        return self.observer

    def correlate(self, call_id: str) -> str:
        return self._call_prefix + call_id

    async def call_tool(self, tool: Any, call_id: str, arguments: dict[str, Any]) -> ToolResolution:
        if tool.name not in self.skill_hooks.tools():
            raise AgentError(
                "invalid_input", "input", "The skill scope does not permit this tool.",
                "Use tools within the active skill's allowed-tools restriction.",
                details={"tool": tool.name},
            )
        await self.skill_hooks.run("PreToolUse", name=tool.name, arguments=arguments)
        resolution = await self.require_observer().call_tool(tool, call_id, arguments)
        await self.skill_hooks.run("PostToolUse", name=tool.name, arguments=arguments,
                                   response=resolution_text(resolution))
        return resolution

    async def delegate(
        self, instruction: str, *, model: str | None = None, model_role: str | None = None,
        tools: tuple[str, ...] | None = None,
        instructions: str | None = None, skill_hooks: tuple[SkillHooks, ...] = (),
        skill_fork: bool = False, allowed_skill_agents: tuple[str, ...] | None = None,
        child_id: str | None = None,
    ) -> str:
        from amplifier_foundation.tracing import generate_sub_session_id

        from .routing import delegated_model

        observer = self.require_observer()
        selected = await delegated_model(
            self.config.provider, observer.model, model=model, role=model_role,
        )
        available = self.skill_hooks.tools()
        if tools is not None and any(name not in available for name in tools):
            raise AgentError(
                "invalid_input", "input", "Delegation requested an unavailable tool.",
                "Delegate with tools from the current tool set.",
            )
        async with self._delegate_lock:
            if observer.cancelled:
                raise asyncio.CancelledError
            child_instructions = self.config.instructions
            if instructions is not None:
                child_instructions = "\n\n".join(value for value in (child_instructions, instructions) if value)
            child = AmplifierRuntime(
                replace(self.config, model=selected, instructions=child_instructions),
                session_id=child_id or generate_sub_session_id(
                    agent_name="skill", parent_session_id=self.session_id
                ),
                parent_id=self.session_id,
                capture=self.capture,
            )
            child.allowed_tools = tools if tools is not None else tuple(sorted(available))
            child.skill_fork = self.skill_fork or skill_fork
            inherited_access = self.allowed_skill_agents
            if inherited_access is None:
                child.allowed_skill_agents = allowed_skill_agents
            elif allowed_skill_agents is None:
                child.allowed_skill_agents = inherited_access
            else:
                child.allowed_skill_agents = tuple(set(inherited_access) & set(allowed_skill_agents))
            if child.allowed_skill_agents is not None and "self" not in child.allowed_skill_agents:
                inherited_tools = self.registry.tools if child.allowed_tools is None else child.allowed_tools
                child.allowed_tools = tuple(name for name in inherited_tools
                                            if name != "delegate")
            child.inherited_skill_hooks = (*self.skill_hooks.active.values(), *skill_hooks)
            if (any(scope.commands for scope in child.inherited_skill_hooks) and child.allowed_tools is not None
                    and "bash" not in child.allowed_tools):
                raise AgentError(
                    "invalid_input", "input", "Delegation excludes bash required by active skill commands.",
                    "Include bash in the delegated tool set while these skill commands are active.",
                )
            child.parent_registry = self.registry
            child._call_prefix = str(uuid.uuid4()) + ":"
            child_observer = DelegatedObserver(observer, selected)
            self._children.add(child)
            try:
                await child.initialize(self._provider_factory)
                if observer.cancelled:
                    raise asyncio.CancelledError
                await child.execute(TurnInput([TextPart(instruction)]), child_observer)
                if child_observer.error is not None:
                    raise PolicyStop()
                return "".join(child_observer.parts)
            finally:
                await child.close()
                self._children.discard(child)

    async def snapshot(self) -> dict[str, Any]:
        messages = copy.deepcopy(await self.context.get_messages())
        snapshot = {
            "version": 1,
            "provider": self.config.provider,
            "messages": messages[self._instruction_count :],
        }
        strict_json(snapshot, "transcript.context")
        return snapshot

    async def restore(self, snapshot: dict[str, Any]) -> None:
        strict_json(snapshot, "transcript.context")
        if snapshot.get("version") != 1 or not isinstance(snapshot.get("messages"), list):
            raise AgentError(
                "internal_failed",
                "session",
                "The saved conversation uses an unsupported context format.",
                "Restore the transcript with a compatible installation.",
            )
        if snapshot.get("provider") != self.config.provider:
            raise AgentError(
                "selector_rejected",
                "selection",
                "The saved conversation belongs to a different provider.",
                "Resume with the original provider or create a new conversation.",
            )
        messages = copy.deepcopy(snapshot["messages"])
        if any(not isinstance(message, dict) or "role" not in message for message in messages):
            raise AgentError(
                "internal_failed",
                "session",
                "The saved conversation contains invalid messages.",
                "Restore an intact transcript before resuming this session.",
            )
        prefix = []
        if self.config.instructions is not None:
            prefix.append({"role": "system", "content": self.config.instructions})
        await self.context.set_messages(prefix + messages)
        self._instruction_count = len(prefix)
        self.context.entry_token = None
        self.context.entry_messages = []
        self._turn_start = len(prefix) + len(messages)

    async def execute(self, input: TurnInput, observer: Observer) -> None:
        self.observer = observer
        self.skill_hooks.begin(self.inherited_skill_hooks)
        self.context.skill_context.clear()
        self._call_ids.clear()
        self.core.coordinator.cancellation.reset()
        self._turn_start = len(await self.context.get_messages())
        entry = self.context.prepare(input)
        try:
            from .skill_agents import selection

            for header in self.skill_hooks.automatic_selection:
                if await selection(self, header) != observer.model:
                    raise AgentError(
                        "invalid_input", "input", "An automatic skill cannot refine the active primary model.",
                        "Remove auto-load and use context: fork to execute the skill on another model.",
                    )
            await self.core.execute(entry)
            if self.context.entry_token is not None:
                raise RuntimeError("The execution did not consume its structured entry")
            await self.skill_hooks.run("Stop")
        except AgentError as error:
            observer.fail(error)
            raise PolicyStop() from error
        finally:
            self.skill_hooks.clear()
            self.context.skill_context.clear()
            self.context.turn_active = False
            self.observer = None

    def request_cancel(self) -> None:
        self.core.coordinator.cancellation.request_immediate()
        for child in tuple(self._children):
            child.request_cancel()

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
            resolution = known.get(self.correlate(call_id))
            if resolution is None and call_id in existing:
                continue
            resolution = resolution or ToolResolution(call_id, "cancelled")
            content = resolution_text(resolution)
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
        if self.response_pending and self.response_chunks:
            messages.append({
                "role": "assistant",
                "content": [{"type": "text", "text": text} for text in self.response_chunks],
            })
        if calls or (self.response_pending and self.response_chunks):
            await self.context.set_messages(messages)
        self.response_pending = False
        self.response_chunks = []

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for child in tuple(self._children):
                await child.close()
            if self.registry is not None:
                await self.registry.close()
        finally:
            try:
                await self.core.cleanup()
            finally:
                if self.provider is not None and callable(getattr(self.provider, "close", None)):
                    await self.provider.close()
