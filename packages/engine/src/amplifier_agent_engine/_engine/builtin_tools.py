"""Adapt Amplifier built-ins to owned results and process lifetimes."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from amplifier_agent_engine._engine.configuration import strict_json
from amplifier_agent_engine._engine.tools import SCHEMA, CapturedToolFailed, RegisteredTool
from amplifier_agent_engine._records import AgentError, ToolContext, ToolFailed, ToolOutcomeUnknown


def result_text(result: Any) -> str:
    if not isinstance(getattr(result, "success", None), bool):
        raise AgentError(
            "tool_result_invalid",
            "executor",
            "The tool returned an invalid result.",
            "Use a tool that returns a boolean success field and JSON or text output.",
        )
    output = result.output
    try:
        strict_json(output, "tool.result")
    except AgentError as error:
        raise AgentError(
            "tool_result_invalid",
            "executor",
            "The tool returned content outside strict JSON.",
            "Return text or finite JSON values from the tool executor.",
        ) from error
    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, allow_nan=False)
    if not result.success:
        error = getattr(result, "error", None)
        message = error.get("message") if isinstance(error, dict) else None
        raise CapturedToolFailed(
            message or (output if isinstance(output, str) else "The tool failed."),
            content,
        )
    return content


def adapt(
    tool: Any,
    *,
    working_directory: Path | None = None,
    read_only_inspection: bool = False,
    bounds: dict[str, int] | None = None,
    zero_defaults: tuple[str, ...] = (),
) -> RegisteredTool:
    limits = bounds or {}

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        return result_text(
            await tool.execute(
                {
                    name: min(value, limits[name]) if name in limits and isinstance(value, int) else value
                    for name, value in arguments.items()
                    if not (name in zero_defaults and value == 0)
                }
            )
        )

    schema: dict[str, Any] = {"$schema": SCHEMA, **copy.deepcopy(tool.input_schema)}
    properties = schema.get("properties", {})
    for name, bound in limits.items():
        properties[name]["description"] = f"{properties[name]['description']} Values above {bound} are read as {bound}."
    description = tool.description
    for name in zero_defaults:
        properties[name]["description"] = (
            re.sub(
                r"\s*Set to 0 for unlimited[^.]*\.",
                "",
                properties[name]["description"],
            )
            + " A value of 0 applies the default limit for the mode."
        )
        description = re.sub(rf"\n- Set explicit `{name}: 0` for unlimited[^\n]*", "", description)
    return RegisteredTool(
        tool.name,
        description,
        schema,
        handler,
        "built-in",
        approval_context={"working_directory": str(working_directory)} if working_directory else None,
        read_only_inspection=read_only_inspection,
    )


WINDOWS_NO_GIT_BASH = (
    "Git Bash was not found. On Windows the bash tool runs commands with Git Bash. "
    "Install Git for Windows from https://git-scm.com/download/win, "
    "or add the folder containing its bash.exe to PATH."
)

# Every byte the shell and its foreground children wrote is in the pipes when the shell exits, so this
# bounds only moving it into the buffers, not waiting for descendants that keep the pipes open.
DRAIN_SECONDS = 0.25


def windows_git_bash() -> str | None:
    """Locate Git Bash, skipping the WSL launchers that usually shadow it on PATH."""
    from amplifier_module_tool_bash import _find_git_bash_executable

    if found := _find_git_bash_executable():
        return found
    on_path = shutil.which("bash")
    if on_path and not any(part in on_path.lower() for part in ("\\system32\\", "\\windowsapps\\")):
        return on_path
    return None


class PipeDrains:
    """Discard what the surviving descendants of finished commands write, until the session closes.

    Closing the read ends instead would kill those descendants with SIGPIPE, or a broken pipe error on
    Windows, the next time they write.
    """

    def __init__(self) -> None:
        self._open: dict[asyncio.SubprocessTransport, list[asyncio.Task[None]]] = {}

    def adopt(self, readers: list[asyncio.Task[None]], transport: asyncio.SubprocessTransport) -> None:
        pending = [reader for reader in readers if not reader.done()]
        if not pending:
            return
        self._open[transport] = pending

        def finished(_: asyncio.Task[None]) -> None:
            if all(reader.done() for reader in pending):
                self._open.pop(transport, None)

        for reader in pending:
            reader.add_done_callback(finished)

    async def close(self) -> None:
        drains, self._open = self._open, {}
        for transport, readers in drains.items():
            for reader in readers:
                reader.cancel()
            transport.close()
        if readers := [reader for pending in drains.values() for reader in pending]:
            await asyncio.wait(readers)


class _ShellProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(limit=2**16, loop=loop)
        # Process.wait() also waits for both pipes to close, which a descendant can hold open.
        self.exited: asyncio.Future[None] = loop.create_future()

    def process_exited(self) -> None:
        super().process_exited()
        if not self.exited.done():
            self.exited.set_result(None)


class _Capture:
    def __init__(self, stream: asyncio.StreamReader | None) -> None:
        self.data = bytearray()
        self.collecting = True
        self.reader = asyncio.create_task(self._read(stream))

    async def _read(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while chunk := await stream.read(2**16):
            if self.collecting:
                self.data.extend(chunk)

    def take(self) -> bytes:
        self.collecting = False
        return bytes(self.data)


def bash_tool(
    runtime: Any, *, directory: Path | None = None, stdin: str | None = None, environment: dict[str, str] | None = None
) -> RegisteredTool:
    from amplifier_module_tool_bash import (
        BashTool,
        _assign_to_windows_job,
        _cleanup_process_tree,
        _spawn_descendant_sweep,
    )

    is_windows = sys.platform == "win32"
    working_directory = str(directory or runtime.config.working_directory)

    def variables() -> dict[str, str]:
        return {**runtime.config.environment, **(environment or {})}

    class CapturedBash(BashTool):
        timed_out: int | None = None
        partial_output: str | None = None

        async def _run_command_background(self, command: str) -> dict[str, Any]:
            if sys.platform == "win32":
                shell = windows_git_bash()
                if shell is None:
                    raise RuntimeError(WINDOWS_NO_GIT_BASH)
                process = subprocess.Popen(
                    [shell, "-c", command],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=self.working_dir,
                    env=variables(),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                process = subprocess.Popen(
                    ["/bin/bash", "-c", command],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=self.working_dir,
                    env=variables(),
                    start_new_session=True,
                )
            return {"pid": process.pid}

        async def _run_command(self, command: str, timeout: int | None = None) -> dict[str, Any]:
            if is_windows:
                shell = windows_git_bash()
                if shell is None:
                    return {"stdout": "", "stderr": WINDOWS_NO_GIT_BASH, "returncode": 1}
            else:
                shell = "/bin/bash"

            async def stop(process: asyncio.subprocess.Process) -> None:
                if is_windows:
                    # Git Bash's bash.exe is a launcher for the real shell, so killing the
                    # launcher alone leaves the command running with the pipes open.
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        capture_output=True,
                        check=False,
                    )
                    return
                # Readers own the pipes, so the module's cleanup must not reap them as well.
                cleanup = asyncio.create_task(
                    _cleanup_process_tree(process, pgid=process.pid, is_windows=False, reap=False)
                )
                cancelled = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                if cancelled:
                    raise asyncio.CancelledError

            async def spawn() -> tuple[asyncio.subprocess.Process, _ShellProtocol, asyncio.SubprocessTransport]:
                loop = asyncio.get_running_loop()
                transport, protocol = await loop.subprocess_exec(
                    lambda: _ShellProtocol(loop),
                    shell,
                    "-c",
                    command,
                    stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.working_dir,
                    env=variables(),
                    start_new_session=not is_windows,
                )
                return asyncio.subprocess.Process(transport, protocol, loop), protocol, transport

            creation = asyncio.create_task(spawn())
            try:
                process, protocol, transport = await asyncio.shield(creation)
            except asyncio.CancelledError:
                # The shell can be running before creation returns. Cancelling creation itself
                # kills only the shell pid and then waits on pipes its children still hold.
                while not creation.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.wait([creation])
                if not creation.cancelled() and creation.exception() is None:
                    process, protocol, transport = creation.result()
                    try:
                        await stop(process)
                        await asyncio.wait([protocol.exited], timeout=DRAIN_SECONDS)
                    finally:
                        transport.close()
                raise
            if is_windows:
                _assign_to_windows_job(process.pid)
                _spawn_descendant_sweep(process.pid)
            captures = (_Capture(process.stdout), _Capture(process.stderr))
            readers = [capture.reader for capture in captures]
            feeding = asyncio.create_task(self._feed(process.stdin))

            def collected() -> tuple[bytes, bytes]:
                feeding.cancel()
                runtime.pipe_drains.adopt(readers, transport)
                stdout, stderr = (capture.take() for capture in captures)
                return stdout, stderr

            limit = timeout if timeout is not None else self.timeout
            try:
                if not (await asyncio.wait([protocol.exited], timeout=limit))[0]:
                    raise TimeoutError
            except (TimeoutError, asyncio.CancelledError) as interruption:
                if isinstance(interruption, TimeoutError):
                    self.timed_out = limit
                try:
                    await stop(process)
                    await asyncio.wait([protocol.exited, *readers], timeout=DRAIN_SECONDS)
                finally:
                    stdout, stderr = collected()
                self.partial_output = json.dumps(
                    {
                        "stdout": stdout.decode(errors="replace"),
                        "stderr": stderr.decode(errors="replace"),
                        "returncode": process.returncode,
                    },
                    ensure_ascii=False,
                )
                raise
            await asyncio.wait(readers, timeout=DRAIN_SECONDS)
            stdout, stderr = collected()
            return {
                "stdout_raw": stdout,
                "stderr_raw": stderr,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
                "returncode": process.returncode,
            }

        @staticmethod
        async def _feed(writer: asyncio.StreamWriter | None) -> None:
            if writer is None or stdin is None:
                return
            # A command that exits without reading its input closes the pipe first.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                writer.write(stdin.encode())
                await writer.drain()
            writer.close()

    def create() -> CapturedBash:
        return CapturedBash(
            {
                "working_dir": working_directory,
                "safety_profile": "unrestricted",
                "require_approval": False,
                "max_output_bytes": 100_000,
            }
        )

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        tool = create()
        result = await tool.execute(arguments)
        if tool.timed_out is not None:
            raise CapturedToolFailed(f"Command timed out after {tool.timed_out} seconds.", tool.partial_output or "")
        return result_text(result)

    schema: dict[str, Any] = {"$schema": SCHEMA, **copy.deepcopy(create().input_schema), "additionalProperties": False}
    schema["properties"]["command"]["minLength"] = 1
    return RegisteredTool(
        "bash",
        BashTool.description.replace("\n- Destructive commands (rm -rf /, sudo rm, etc.) are blocked.", ""),
        schema,
        handler,
        "built-in",
        approval_context={"working_directory": working_directory},
    )


def delegate_tool(runtime: Any) -> RegisteredTool:
    from amplifier_module_tool_delegate import DelegateTool

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        output: list[str] = []
        failures: list[AgentError] = []

        async def spawn(**kwargs: Any) -> dict[str, Any]:
            try:
                text = await runtime.delegate(
                    kwargs["instruction"],
                    model=arguments.get("model"),
                    model_role=arguments.get("model_role"),
                    tools=tuple(arguments["tools"]) if "tools" in arguments else None,
                    child_id=kwargs.get("sub_session_id"),
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
        tool = DelegateTool(
            coordinator,
            {
                "features": {"session_resume": {"enabled": False}},
                "settings": {"timeout": None},
            },
        )
        result = await tool.execute(
            {
                "agent": "self",
                "instruction": arguments["instruction"],
                "context_depth": "none",
            }
        )
        if failures:
            raise failures[0]
        result_text(result)
        if len(output) != 1:
            raise ToolOutcomeUnknown("Delegation did not return one authoritative result.")
        return output[0]

    return RegisteredTool(
        "delegate",
        "Delegate a task within this agent's authority.",
        {
            "$schema": SCHEMA,
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "instruction": {"type": "string", "minLength": 1},
                "model": {"type": "string", "minLength": 1},
                "model_role": {"type": "string", "enum": ["general", "economy"]},
                "tools": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            "required": ["instruction"],
            "not": {"required": ["model", "model_role"]},
        },
        handler,
        "built-in",
    )


def uses_patch(model: str) -> bool:
    """Whether a request to this model offers apply_patch in place of edit_file."""
    return "gpt-" in model and "oss" not in model and "gpt-4" not in model


def builtin_tools(runtime: Any) -> list[RegisteredTool]:
    from amplifier_module_tool_apply_patch.tool import ApplyPatchTool
    from amplifier_module_tool_filesystem import EditTool, ReadTool, WriteTool
    from amplifier_module_tool_search.glob import GlobTool
    from amplifier_module_tool_search.grep import GrepTool
    from amplifier_module_tool_web import WebFetchTool, WebSearchTool

    class TruthfulSearch(WebSearchTool):
        async def _mock_search(self, query: str) -> list[Any]:
            raise ToolFailed("Web search failed. Check network access before trying again.")

    config = {"working_dir": str(runtime.config.working_directory)}
    writable = {
        **config,
        "allowed_write_paths": [
            str(path) for path in (runtime.config.working_directory, *runtime.config.additional_directories)
        ],
    }
    coordinator = runtime.core.coordinator
    tools = [
        (ReadTool(config, coordinator), {"limit": 2000}, ()),
        (WriteTool(writable, coordinator), {}, ()),
        (EditTool(writable, coordinator), {}, ()),
        (ApplyPatchTool({**writable, "engine": "function"}, coordinator), {}, ()),
        (GlobTool(config), {}, ()),
        (
            GrepTool({**config, "max_result_bytes": 100_000, "max_line_chars": 2000}),
            {"head_limit": 500},
            ("head_limit",),
        ),
        (WebFetchTool({**config, "blocked_domains": [], "default_limit": 200 * 1024}), {"limit": 200 * 1024}, ()),
        (TruthfulSearch(config), {}, ()),
    ]
    return [
        *(
            adapt(
                tool,
                working_directory=runtime.config.working_directory,
                bounds=bounds,
                zero_defaults=zero_defaults,
                read_only_inspection=isinstance(tool, (ReadTool, GlobTool, GrepTool)),
            )
            for tool, bounds, zero_defaults in tools
        ),
        bash_tool(runtime),
        delegate_tool(runtime),
    ]
