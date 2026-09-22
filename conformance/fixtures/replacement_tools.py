"""Independent local and MCP executors for replacement acceptance."""

import asyncio
import glob
import json
import os
import re
import signal
from pathlib import Path

import httpx
import yaml
from amplifier_agent_engine._records import ToolFailed, ToolOutcomeUnknown

NAMED = ("read_file", "write_file", "edit_file", "glob", "grep", "bash", "web_fetch", "web_search",
         "delegate")
BUILTINS = {"read_file", "write_file", "bash", "glob", "grep", "web_fetch", "delegate", "load_skill"}
INSPECTION = {"read_file", "glob", "grep"}
SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}


class PartialFailed(ToolFailed):
    def __init__(self, message, content):
        super().__init__(message)
        self.content = content


class PartialUnknown(ToolOutcomeUnknown):
    def __init__(self, message, content):
        super().__init__(message)
        self.content = content


def skill(sources, name):
    for source in sources or []:
        root = Path(source)
        paths = [root] if root.is_file() else sorted(root.rglob("SKILL.md"))
        for path in paths:
            text = path.read_text()
            if not text.startswith("---\n"):
                continue
            _, header, body = text.split("---", 2)
            metadata = yaml.safe_load(header) or {}
            if metadata.get("name", path.parent.name) != name:
                continue
            agent = {}
            if agent_name := metadata.get("agent"):
                for folder in (root, *path.parents):
                    descriptor = folder / "agents" / f"{agent_name}.md"
                    if descriptor.is_file():
                        _, frontmatter, _ = descriptor.read_text().split("---", 2)
                        agent = yaml.safe_load(frontmatter) or {}
                        break
            return metadata, body.strip(), agent, path.parent
    raise ToolFailed(f"No supplied skill has the name {name}.")


async def local(name, arguments, *, cwd, environment):
    def path(value):
        value = Path(value).expanduser()
        return value if value.is_absolute() else Path(cwd) / value

    if name == "read_file":
        return path(arguments["file_path"]).read_text()
    if name == "write_file":
        target = path(arguments["file_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(arguments["content"])
        return f"Wrote {target}."
    if name == "glob":
        return "\n".join(sorted(glob.glob(arguments.get("pattern", "*"), root_dir=path(arguments.get("path", ".")), recursive=True)))
    if name == "grep":
        pattern = re.compile(arguments["pattern"])
        root = path(arguments.get("path", "."))
        paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        return "\n".join(f"{path}:{number}:{line}" for path in paths
                         for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
                         if pattern.search(line))
    if name == "web_fetch":
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(arguments["url"])
            response.raise_for_status()
            return response.text
    if name != "bash":
        raise ToolFailed(f"No local executor has the name {name}.")
    process = await asyncio.create_subprocess_shell(
        arguments["command"], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE, cwd=path(arguments.get("cwd", ".")),
        env={**environment, **arguments.get("environment", {})}, start_new_session=True,
    )
    collecting = asyncio.create_task(process.communicate(arguments.get("stdin", "").encode()))
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(collecting), timeout=arguments.get("timeout", 30))
    except (asyncio.CancelledError, TimeoutError):
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = await asyncio.shield(collecting)
        content = json.dumps({"stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace"), "returncode": process.returncode})
        raise PartialUnknown("The command stopped before its outcome was established.", content) from None
    text = (stdout + stderr).decode(errors="replace")
    if process.returncode:
        content = json.dumps({"stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace"), "returncode": process.returncode})
        raise PartialFailed(text or f"The command exited with code {process.returncode}.", content)
    return text


class Mcp:
    def __init__(self, declaration):
        self.declaration = declaration
        self.sequence = 0
        self.lock = asyncio.Lock()
        self.process = self.client = None
        self.session_id = None

    async def open(self):
        if self.declaration.transport == "stdio":
            self.process = await asyncio.create_subprocess_exec(
                self.declaration.command, *(self.declaration.args or []),
                env={**os.environ, **(self.declaration.env or {})},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            self.client = httpx.AsyncClient(headers=self.declaration.headers or {})
        await self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                          "clientInfo": {"name": "replacement", "version": "1"}})
        await self.request("notifications/initialized", {}, notify=True)
        return (await self.request("tools/list", {}))["tools"]

    async def request(self, method, params, *, notify=False):
        async with self.lock:
            self.sequence += 1
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notify:
                message["id"] = self.sequence
            if self.process is not None:
                if self.process.returncode is not None:
                    raise ToolOutcomeUnknown("The MCP connection ended without a result.")
                self.process.stdin.write((json.dumps(message) + "\n").encode())
                await self.process.stdin.drain()
                if notify:
                    return None
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        raise ToolOutcomeUnknown("The MCP connection ended without a result.")
                    response = json.loads(line)
                    if response.get("id") == self.sequence:
                        break
            else:
                headers = {"Accept": "application/json, text/event-stream"}
                if self.session_id:
                    headers["Mcp-Session-Id"] = self.session_id
                reply = await self.client.post(self.declaration.url, json=message, headers=headers)
                reply.raise_for_status()
                self.session_id = reply.headers.get("Mcp-Session-Id", self.session_id)
                if notify:
                    return None
                if "text/event-stream" in reply.headers.get("content-type", ""):
                    response = next(json.loads(line[5:].strip()) for line in reply.text.splitlines() if line.startswith("data:"))
                else:
                    response = reply.json()
            if "error" in response:
                raise ToolFailed(response["error"].get("message", "The MCP server rejected the request."))
            return response["result"]

    async def call(self, name, arguments):
        try:
            result = await self.request("tools/call", {"name": name, "arguments": arguments})
        except (BrokenPipeError, ConnectionError, httpx.HTTPError):
            raise ToolOutcomeUnknown("The MCP connection ended before reporting its effect.") from None
        text = "".join(part["text"] for part in result.get("content", []) if part.get("type") == "text")
        if result.get("isError"):
            content = json.dumps({key: value for key, value in result.items() if key != "isError"})
            raise PartialFailed(text or "The MCP executor rejected the effect.", content)
        return text

    async def close(self):
        if self.process is not None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.client is not None:
            await self.client.aclose()
