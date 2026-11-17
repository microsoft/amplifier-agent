"""Keep MCP transport ownership separate from tool execution policy."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy
from dataclasses import asdict
import json
from pathlib import Path
import re
from typing import Any

from amplifier_agent_engine._engine.bash_approval import PRODUCER, digest, identity
from amplifier_agent_engine._engine.configuration import IMAGE_MEDIA_TYPES, strict_json
from amplifier_agent_engine._engine.images import describe, result_parts
from amplifier_agent_engine._engine.tools import SCHEMA, CapturedToolFailed, PreparedTool, RegisteredTool, ToolRegistry
from amplifier_agent_engine._records import (
    AgentError,
    ContentPart,
    ImagePart,
    McpServer,
    ReusableApproval,
    TextPart,
    ToolContext,
    ToolFailed,
    ToolOutcomeUnknown,
    _ToolNotExecutedError,
)


def _image_parts(blocks: list[dict[str, Any]], structured: Any) -> list[ContentPart]:
    """Carry MCP image content as image parts, text as text, and any other block or the
    structured content as its JSON text, in order."""
    parts: list[ContentPart] = []
    for block in blocks:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(TextPart(block["text"]))
        elif block.get("type") == "image":
            media_type = block.get("mimeType")
            if not isinstance(media_type, str) or media_type not in IMAGE_MEDIA_TYPES:
                raise AgentError(
                    "tool_result_invalid",
                    "executor",
                    f"The MCP server returned image content of unregistered media type {media_type!r}.",
                    f"Have the server return images as one of {', '.join(IMAGE_MEDIA_TYPES)}.",
                    details={"media_type": media_type},
                )
            parts.append(ImagePart(media_type=media_type, data=block.get("data", "")))
        else:
            parts.append(TextPart(json.dumps(block, ensure_ascii=False, allow_nan=False)))
    if structured is not None:
        parts.append(TextPart(json.dumps({"structured_content": structured}, ensure_ascii=False, allow_nan=False)))
    return result_parts(parts)


class MCPConnection:
    def __init__(self, server: McpServer, environment: dict[str, str], working_directory: Path) -> None:
        self.server = copy.deepcopy(server)
        self.environment = dict(environment)
        self.working_directory = working_directory
        self.client: Any = None
        self.tools: list[dict[str, Any]] = []
        self.ready = asyncio.Event()
        self.shutdown = asyncio.Event()
        self.error: BaseException | None = None
        self.task = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        from amplifier_module_tool_mcp.client import MCPClient
        from amplifier_module_tool_mcp.streamable_http_client import MCPStreamableHTTPClient

        try:
            if self.server.transport == "stdio":
                assert self.server.command is not None
                self.client = MCPClient(
                    self.server.name,
                    self.server.command,
                    list(self.server.args or []),
                    env={**self.environment, **(self.server.env or {})},
                    verbose_servers=True,
                    cwd=self.working_directory,
                    inherit_environment=False,
                    discovery="tools",
                    read_timeout_seconds=120.0,
                    redact_connection_details=True,
                )
            else:
                assert self.server.url is not None
                self.client = MCPStreamableHTTPClient(
                    self.server.name,
                    self.server.url,
                    headers=dict(self.server.headers or {}),
                    discovery="tools",
                    read_timeout_seconds=120.0,
                    redact_connection_details=True,
                )
            await self.client.connect()
            self.tools = copy.deepcopy(self.client.get_tools())
            self.ready.set()
            await self.shutdown.wait()
        except BaseException as error:
            self.error = error
        finally:
            try:
                if self.client is not None:
                    await self.client.disconnect()
            finally:
                self.ready.set()

    async def connect(self) -> None:
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=30)
        except TimeoutError:
            await self.close()
        if self.client is None or not self.client.is_connected:
            raise AgentError(
                "engine_unavailable",
                "lifecycle",
                f"MCP server {self.server.name} could not connect.",
                "Check the server command or HTTP URL and its authentication settings.",
                details={"server": self.server.name},
            ) from self.error

    async def close(self) -> None:
        self.shutdown.set()
        if self.client is not None:
            self.client.retire()
        if not self.ready.is_set():
            self.task.cancel()
        await asyncio.shield(self.task)

    async def call(self, name: str, arguments: dict[str, Any]) -> str | list[ContentPart]:
        from amplifier_module_tool_mcp.sdk_compat import MCPProtocolError, sdk_field

        if self.client is None or not self.client.is_connected or self.shutdown.is_set():
            raise AgentError(
                "tool_callback_failed",
                "executor",
                "The MCP connection is unavailable.",
                "Reconnect by constructing a new agent before starting another tool call.",
            )
        try:
            result = await self.client.call_tool(name, arguments=arguments, reconnect=False)
        except MCPProtocolError as error:
            if error.code not in (-32700, -32600, -32601, -32602, -32603):
                raise ToolOutcomeUnknown(
                    "The MCP call ended without a tool result. Inspect the effect before retrying."
                ) from error
            raise ToolFailed(error.message) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise ToolOutcomeUnknown(
                "The MCP connection ended without an authoritative result. Inspect the effect."
            ) from error
        try:
            failed = sdk_field(result, "is_error", "isError")
            if not isinstance(failed, bool):
                raise ValueError("Invalid MCP error flag")
            content = result.content
            if not isinstance(content, list):
                raise ValueError("Invalid MCP content")
            blocks = [block.model_dump(mode="json", by_alias=True, exclude_none=True) for block in content]
            structured = sdk_field(result, "structured_content", "structuredContent", default=None)
            payload = {"content": blocks}
            if structured is not None:
                payload["structured_content"] = structured
            strict_json(payload, "mcp.result")
        except Exception as error:
            raise AgentError(
                "tool_result_invalid",
                "executor",
                "The MCP server returned a malformed result.",
                "Correct the server's tool result before attempting another effect.",
            ) from error
        if any(block.get("type") == "image" for block in blocks):
            parts = _image_parts(blocks, structured)
            if failed:
                described = describe(parts)
                raise CapturedToolFailed(described, described)
            return parts
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if failed:
            raise CapturedToolFailed(text, text)
        return text


def prepare_invocation(
    runtime: Any,
    connection: MCPConnection,
    registered: RegisteredTool,
    original: str,
    arguments: dict[str, Any],
    allowed: Callable[[], bool],
) -> PreparedTool | None:
    """Prepare one public client scope; the engine retains no remembered policy."""
    client = connection.client
    frozen = copy.deepcopy(arguments)
    active = True
    consumed = False

    def facts() -> dict[str, Any]:
        if (
            not active
            or connection.shutdown.is_set()
            or connection.client is not client
            or client is None
            or not client.is_connected
            or runtime.registry.tools.get(registered.name) is not registered
        ):
            raise ValueError("The MCP executor is no longer current")
        binding = client.approval_binding()
        if (
            type(binding) is not dict
            or type(binding.get("version")) is not int
            or binding.get("version") != 1
            or not isinstance(binding.get("key"), str)
            or re.fullmatch(r"[a-f0-9]{64}", binding["key"]) is None
            or (
                connection.server.transport == "http"
                and (type(binding.get("httpSendAuthorization")) is not int or binding.get("httpSendAuthorization") != 1)
            )
        ):
            raise ValueError("The MCP connection has no supported invocation witness")
        definitions = [item for item in client.get_tools() if item.get("name") == original]
        if len(definitions) != 1:
            raise ValueError("The MCP tool registry changed")
        schema = {"$schema": SCHEMA, **definitions[0]["input_schema"]}
        if schema != registered.input_schema:
            raise ValueError("The MCP tool schema changed")
        servers = [server for server in runtime.config.mcp_servers if server.name == connection.server.name]
        if (
            len(servers) != 1
            or servers[0] != connection.server
            or runtime.config.environment != connection.environment
            or runtime.config.working_directory != connection.working_directory
        ):
            raise ValueError("The configured MCP server changed")
        return {
            "connection": copy.deepcopy(binding),
            "tool": original,
            "arguments": frozen,
            "schema": copy.deepcopy(schema),
            "server": asdict(servers[0]),
            "environment": dict(runtime.config.environment),
            "directory": identity(runtime.config.working_directory, directory=True),
            "configuration": {
                "tool_error_policy": runtime.config.tool_error_policy,
                "tool_result_max_bytes": runtime.config.tool_result_max_bytes,
                "additional_directories": [
                    identity(path, directory=True) for path in runtime.config.additional_directories
                ],
            },
        }

    try:
        expected = copy.deepcopy(facts())

        def current() -> bool:
            try:
                return active and allowed() is True and facts() == expected
            except Exception:
                return False

        # Constructing the public scope also rejects older APIs without the
        # synchronous owner veto. Enter it only after the ordinary approval.
        scope = client.approval_scope(expected["connection"], original, frozen, is_current=current)
        approval = ReusableApproval(
            1,
            PRODUCER,
            digest({"producer": PRODUCER, "mcp": expected}),
            "Call this exact MCP tool with the same connection, arguments and configured authority.",
            "request",
        )
    except (AttributeError, KeyError, TypeError, OSError, ValueError, RuntimeError):
        return None

    async def execute(arguments: dict[str, Any], context: ToolContext) -> str | list[ContentPart]:
        nonlocal active, consumed
        if consumed or not current():
            active = False
            raise _ToolNotExecutedError(
                "The prepared MCP authority changed or expired. Request a new approval before calling this tool.",
                "Start another turn to request approval for the current invocation.",
            )
        consumed = True
        try:
            with scope:
                return await connection.call(original, copy.deepcopy(frozen))
        finally:
            active = False

    return PreparedTool(approval, execute)


def registered_mcp_tool(runtime: Any, connection: MCPConnection, definition: dict[str, Any]) -> RegisteredTool:
    original = definition["name"]
    name = f"mcp_{connection.server.name}_" + re.sub(r"[^a-zA-Z0-9_-]", "_", original)

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str | list[ContentPart]:
        return await connection.call(original, arguments)

    def prepare(arguments: dict[str, Any], allowed: Callable[[], bool]) -> PreparedTool | None:
        return prepare_invocation(runtime, connection, registered, original, arguments, allowed)

    registered = RegisteredTool(
        name,
        definition.get("description") or f"Call {original} on {connection.server.name}.",
        {"$schema": SCHEMA, **copy.deepcopy(definition["input_schema"])},
        handler,
        "mcp",
        prepare=prepare,
        mcp_identity=(connection.server.name, original),
    )
    return registered


async def prepare_mcp(runtime: Any, registry: ToolRegistry) -> None:
    allowed = getattr(runtime, "allowed_tools", None)
    parent = getattr(runtime, "parent_registry", None)
    if parent is not None:
        for tool in parent.tools.values():
            if tool.source == "mcp" and (allowed is None or tool.name in allowed):
                registry.add(tool)
        return
    for server in runtime.config.mcp_servers:
        prefix = f"mcp_{server.name}_"
        if allowed is not None and not any(name.startswith(prefix) for name in allowed):
            continue
        client = MCPConnection(server, dict(runtime.config.environment), runtime.config.working_directory)
        registry.cleanups.append(client.close)
        await client.connect()
        for definition in client.tools:
            tool = registered_mcp_tool(runtime, client, definition)
            if allowed is not None and tool.name not in allowed:
                continue
            registry.add(tool)
