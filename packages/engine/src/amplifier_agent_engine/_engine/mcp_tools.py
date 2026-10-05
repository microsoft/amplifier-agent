"""Keep MCP transport ownership separate from tool execution policy."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from importlib.metadata import version
import json
from pathlib import Path
import re
from typing import Any

from amplifier_agent_engine._engine.configuration import IMAGE_MEDIA_TYPES, strict_json
from amplifier_agent_engine._engine.images import describe, result_parts
from amplifier_agent_engine._engine.tools import SCHEMA, CapturedToolFailed, RegisteredTool, ToolRegistry
from amplifier_agent_engine._records import (
    AgentError,
    ContentPart,
    ImagePart,
    McpServer,
    TextPart,
    ToolContext,
    ToolFailed,
    ToolOutcomeUnknown,
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
        self.server = server
        self.environment = environment
        self.working_directory = working_directory
        self.session: Any = None
        self.tools: list[dict[str, Any]] = []
        self.ready = asyncio.Event()
        self.shutdown = asyncio.Event()
        self.error: BaseException | None = None
        self.task = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        from amplifier_module_tool_mcp.discovery import discover_tools
        from amplifier_module_tool_mcp.sdk_compat import build_client_info, negotiate
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS, stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        try:
            async with AsyncExitStack() as stack:
                if self.server.transport == "stdio":
                    assert self.server.command is not None
                    environment = {
                        **dict.fromkeys(DEFAULT_INHERITED_ENV_VARS, ""),
                        **self.environment,
                        **(self.server.env or {}),
                    }
                    parameters = StdioServerParameters(
                        command=self.server.command,
                        args=self.server.args or [],
                        env=environment,
                        cwd=self.working_directory,
                    )
                    streams = await stack.enter_async_context(stdio_client(parameters))
                else:
                    assert self.server.url is not None
                    client = await stack.enter_async_context(
                        create_mcp_http_client(
                            headers=self.server.headers or {},
                        )
                    )
                    streams = await stack.enter_async_context(
                        streamable_http_client(
                            self.server.url,
                            http_client=client,
                        )
                    )
                read_timeout: Any = 120.0 if int(version("mcp").split(".")[0]) >= 2 else timedelta(seconds=120)
                session = await stack.enter_async_context(
                    ClientSession(
                        streams[0],
                        streams[1],
                        client_info=build_client_info(),
                        read_timeout_seconds=read_timeout,
                    )
                )
                await negotiate(session, server_name=self.server.name)
                self.tools = await discover_tools(session)
                self.session = session
                self.ready.set()
                await self.shutdown.wait()
        except BaseException as error:
            self.error = error
        finally:
            self.session = None
            self.ready.set()

    async def connect(self) -> None:
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=30)
        except TimeoutError:
            await self.close()
        if self.session is None:
            raise AgentError(
                "engine_unavailable",
                "lifecycle",
                f"MCP server {self.server.name} could not connect.",
                "Check the server command or HTTP URL and its authentication settings.",
                details={"server": self.server.name},
            ) from self.error

    async def close(self) -> None:
        self.shutdown.set()
        if not self.ready.is_set():
            self.task.cancel()
        await asyncio.shield(self.task)

    async def call(self, name: str, arguments: dict[str, Any]) -> str | list[ContentPart]:
        from amplifier_module_tool_mcp.sdk_compat import MCP_ERROR_CLASS, describe_mcp_error, sdk_field

        if self.session is None:
            raise AgentError(
                "tool_callback_failed",
                "executor",
                "The MCP connection is unavailable.",
                "Reconnect by constructing a new agent before starting another tool call.",
            )
        try:
            result = await self.session.call_tool(name, arguments=arguments)
        except MCP_ERROR_CLASS as error:
            description = describe_mcp_error(error)
            if description["code"] not in (-32700, -32600, -32601, -32602, -32603):
                raise ToolOutcomeUnknown(
                    "The MCP call ended without a tool result. Inspect the effect before retrying."
                ) from error
            raise ToolFailed(str(error)) from error
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
            original = definition["name"]
            name = prefix + re.sub(r"[^a-zA-Z0-9_-]", "_", original)
            if allowed is not None and name not in allowed:
                continue

            async def handler(
                arguments: dict[str, Any],
                context: ToolContext,
                client: MCPConnection = client,
                original: str = original,
            ) -> str | list[ContentPart]:
                return await client.call(original, arguments)

            registry.add(
                RegisteredTool(
                    name,
                    definition.get("description") or f"Call {original} on {server.name}.",
                    {"$schema": SCHEMA, **definition["input_schema"]},
                    handler,
                    "mcp",
                )
            )
