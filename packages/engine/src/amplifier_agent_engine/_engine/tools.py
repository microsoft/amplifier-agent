"""Assemble executors behind the engine's source-labelled tool boundary."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from .._records import AgentError, ToolContext, ToolFailed, ToolOutcomeUnknown

SCHEMA = "https://json-schema.org/draft/2020-12/schema"


class CapturedToolFailed(ToolFailed):
    def __init__(self, message: str, content: str) -> None:
        super().__init__(message)
        self.content = content


class CapturedToolUnknown(ToolOutcomeUnknown):
    def __init__(self, message: str, content: str) -> None:
        super().__init__(message)
        self.content = content


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[str]]
    source: Literal["built-in", "caller", "mcp"]
    safety: dict[str, Any] | None = None
    deadline: datetime | None = None
    approval_context: dict[str, Any] | None = None
    read_only_inspection: bool = False
    guard: bool = False


@dataclass
class ToolRegistry:
    tools: dict[str, RegisteredTool] = field(default_factory=dict)
    cleanups: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def add(self, tool: RegisteredTool) -> None:
        if tool.name in self.tools:
            raise AgentError(
                "invalid_input", "input", f"Duplicate tool name: {tool.name}.",
                "Give caller and MCP tools names distinct from the built-in tools.",
                details={"tool": tool.name},
            )
        try:
            validator_for(tool.input_schema).check_schema(tool.input_schema)
        except SchemaError as error:
            raise AgentError(
                "invalid_input", "input", f"Invalid input schema for tool {tool.name}.",
                "Correct the tool's JSON Schema before constructing the agent.",
            ) from error
        self.tools[tool.name] = tool

    async def close(self) -> None:
        errors = []
        while self.cleanups:
            cleanup = self.cleanups.pop()
            try:
                await cleanup()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Tool connections could not be closed.", errors)


async def prepare_tools(runtime: Any) -> ToolRegistry:
    from .builtin_tools import builtin_tools
    from .mcp_tools import prepare_mcp
    from .skill_tools import prepare_skills

    registry = ToolRegistry()
    allowed = getattr(runtime, "allowed_tools", None)

    def include(tool: RegisteredTool) -> None:
        if allowed is None or tool.name in allowed:
            registry.add(tool)

    try:
        for tool in runtime.config.tools:
            include(RegisteredTool(
                tool.name, tool.description, copy.deepcopy(tool.input_schema), tool.handler,
                "caller", copy.deepcopy(tool.safety),
            ))
        for tool in builtin_tools(runtime):
            include(tool)
        for tool in await prepare_skills(runtime):
            include(tool)
        await prepare_mcp(runtime, registry)
        if allowed is not None:
            unknown = set(allowed) - registry.tools.keys()
            if unknown:
                raise AgentError(
                    "invalid_input", "input", "Delegation requested unavailable tools.",
                    "Select names from the current agent's tool set.",
                    details={"tools": sorted(unknown)},
                )
        return registry
    except BaseException:
        await registry.close()
        raise
