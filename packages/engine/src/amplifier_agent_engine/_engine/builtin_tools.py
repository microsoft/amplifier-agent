"""Adapt Amplifier built-ins to owned results and process lifetimes."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

from .._records import AgentError, ToolContext, ToolFailed, ToolOutcomeUnknown
from .configuration import strict_json
from .tools import SCHEMA, CapturedToolFailed, CapturedToolUnknown, RegisteredTool


def result_text(result: Any) -> str:
    if not isinstance(getattr(result, "success", None), bool):
        raise AgentError(
            "tool_result_invalid", "executor", "The tool returned an invalid result.",
            "Use a tool that returns a boolean success field and JSON or text output.",
        )
    output = result.output
    try:
        strict_json(output, "tool.result")
    except AgentError as error:
        raise AgentError(
            "tool_result_invalid", "executor", "The tool returned content outside strict JSON.",
            "Return text or finite JSON values from the tool executor.",
        ) from error
    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, allow_nan=False)
    if not result.success:
        error = getattr(result, "error", None)
        message = error.get("message") if isinstance(error, dict) else None
        raise CapturedToolFailed(
            message or (output if isinstance(output, str) else "The tool failed."), content,
        )
    return content


def adapt(tool: Any, *, working_directory: Path | None = None,
          read_only_inspection: bool = False) -> RegisteredTool:
    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        return result_text(await tool.execute(arguments))

    schema = {"$schema": SCHEMA, **copy.deepcopy(tool.input_schema)}
    return RegisteredTool(
        tool.name, tool.description, schema, handler, "built-in",
        approval_context={"working_directory": str(working_directory)} if working_directory else None,
        read_only_inspection=read_only_inspection,
    )


def bash_tool(runtime: Any, *, directory: Path | None = None, stdin: str | None = None,
              environment: dict[str, str] | None = None) -> RegisteredTool:
    from amplifier_module_tool_bash import BashTool, _await_process_tree_cleanup

    class CapturedBash(BashTool):
        uncertain = False
        partial_output: str | None = None

        async def _run_command(self, command: str, timeout: int | None = None) -> dict[str, Any]:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash", "-c", command,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir, env={**runtime.config.environment, **(environment or {})},
                start_new_session=True,
            )
            communication = asyncio.create_task(
                process.communicate(stdin.encode() if stdin is not None else None)
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communication),
                    timeout=timeout if timeout is not None else 30,
                )
            except (TimeoutError, asyncio.CancelledError):
                self.uncertain = True
                await _await_process_tree_cleanup(process, pgid=process.pid, is_windows=False)
                stdout, stderr = await communication
                self.partial_output = json.dumps({
                    "stdout": stdout.decode(errors="replace"),
                    "stderr": stderr.decode(errors="replace"),
                    "returncode": process.returncode,
                }, ensure_ascii=False)
                raise
            return {
                "stdout_raw": stdout, "stderr_raw": stderr,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"), "returncode": process.returncode,
            }

        def _truncate_output(self, output: str) -> tuple[str, bool, int]:
            return output, False, len(output.encode())

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        tool = CapturedBash({
            "working_dir": str(directory or runtime.config.working_directory),
            "safety_profile": "unrestricted", "require_approval": False,
        })
        result = await tool.execute(arguments)
        if tool.uncertain:
            raise CapturedToolUnknown(
                "The command timed out after execution began.", tool.partial_output or "",
            )
        return result_text(result)

    return RegisteredTool("bash", "Execute a shell command and wait for its result.", {
        "$schema": SCHEMA, "type": "object", "additionalProperties": False,
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
        }, "required": ["command"],
    }, handler, "built-in", approval_context={
        "working_directory": str(directory or runtime.config.working_directory),
    })


def delegate_tool(runtime: Any) -> RegisteredTool:
    from amplifier_module_tool_delegate import DelegateTool

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        output: list[str] = []
        failures: list[AgentError] = []

        async def spawn(**kwargs: Any) -> dict[str, Any]:
            try:
                text = await runtime.delegate(
                    kwargs["instruction"], model=arguments.get("model"),
                    model_role=arguments.get("model_role"),
                    tools=tuple(arguments["tools"]) if "tools" in arguments else None,
                )
            except AgentError as error:
                failures.append(error)
                raise
            output.append(text)
            return {"output": text, "session_id": kwargs["sub_session_id"]}

        class Coordinator:
            def __getattr__(self, name: str) -> Any:
                return getattr(runtime.core.coordinator, name)

            def get_capability(self, name: str) -> Any:
                if name == "session.spawn":
                    return spawn
                return runtime.core.coordinator.get_capability(name)

        coordinator: Any = Coordinator()
        tool = DelegateTool(coordinator, {
            "features": {"session_resume": {"enabled": False}},
            "settings": {"timeout": None},
        })
        result = await tool.execute({
            "agent": "self", "instruction": arguments["instruction"], "context_depth": "none",
        })
        if failures:
            raise failures[0]
        result_text(result)
        if len(output) != 1:
            raise ToolOutcomeUnknown("Delegation did not return one authoritative result.")
        return output[0]

    return RegisteredTool("delegate", "Delegate a task within this agent's authority.", {
        "$schema": SCHEMA, "type": "object", "additionalProperties": False,
        "properties": {
            "instruction": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "model_role": {"type": "string", "enum": ["general", "economy"]},
            "tools": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        }, "required": ["instruction"], "not": {"required": ["model", "model_role"]},
    }, handler, "built-in")


def builtin_tools(runtime: Any) -> list[RegisteredTool]:
    from amplifier_module_tool_filesystem import EditTool, ReadTool, WriteTool
    from amplifier_module_tool_search.glob import GlobTool
    from amplifier_module_tool_search.grep import GrepTool
    from amplifier_module_tool_web import WebFetchTool, WebSearchTool

    class TruthfulSearch(WebSearchTool):
        async def _mock_search(self, query: str) -> list[Any]:
            raise ToolFailed("Web search failed. Check network access before trying again.")

    config = {"working_dir": str(runtime.config.working_directory)}
    coordinator = runtime.core.coordinator
    tools = [
        ReadTool(config, coordinator), WriteTool(config, coordinator), EditTool(config, coordinator),
        GlobTool(config), GrepTool(config), WebFetchTool({**config, "blocked_domains": []}),
        TruthfulSearch(config),
    ]
    return [
        *(adapt(tool, working_directory=runtime.config.working_directory,
                read_only_inspection=isinstance(tool, (ReadTool, GlobTool, GrepTool))) for tool in tools),
        bash_tool(runtime), delegate_tool(runtime),
    ]
