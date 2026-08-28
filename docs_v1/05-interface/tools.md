# Tools

Tools are how the agent acts beyond generating text. Every call resolves to one
of three sources:

- **`builtin`** tools the agent ships with, covering the filesystem, shell, and
  web.
- **`host`** tools you supply as Python callables.
- **`mcp`** tools proxied from an MCP server you configured.

All three present the same interface to the model. It sees one flat set of named,
described, schema-typed tools and cannot tell which source a tool came from. The
`source` field on a `tool/call` event is the only discriminator, and it is for
you, not the model.

## Host tools

```python
@dataclass(frozen=True)
class HostTool:
    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: Callable[[Mapping[str, object]], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False
    details: Mapping[str, object] | None = None
```

- **`name`** is what the model calls. It is unique across the combined builtin,
  host, and MCP set.
- **`description`** is shown to the model. It is the main thing determining
  whether the tool gets used correctly, so write it for the model rather than for
  a code reviewer.
- **`input_schema`** is a JSON Schema mapping describing the arguments.
- **`handler`** is an async callable. The agent awaits it with the call's
  arguments and expects a `ToolResult` back.

```python
async def open_ticket(args: Mapping[str, object]) -> ToolResult:
    ticket_id = await tracker.create(title=args["title"])
    return ToolResult(content=f"Opened {ticket_id}", details={"id": ticket_id})


ToolsConfig(host_tools=[
    HostTool(
        name="open_ticket",
        description="File a bug report in the issue tracker.",
        input_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        handler=open_ticket,
    )
])
```

`content` is what the model sees. `details` is structured data for your own code,
carried on the `tool/result` event and not guaranteed to reach the model. Put the
human-readable answer in `content` and the machine-readable one in `details`.

There is no yield-and-resume protocol. The handler returns or it raises.

## MCP tools

Servers are configured on [`AgentConfig.mcp_servers`](../03-configuration.md#mcp_servers).
Their tools join the same flat set and are subject to the same filtering.

## Allow and deny

```python
ToolsConfig(deny=["shell"])                  # everything except the shell
ToolsConfig(allow=["read_file", "grep"])     # read-only
```

- **`allow=None`** leaves the default set intact.
- **`allow=[...]`** restricts the set to those names.
- **`deny`** always wins. A name in both is denied.

Filtering applies to names across all three sources, so a deny list is the way to
remove an MCP tool you do not want as easily as a built-in one.

A filtered-out tool is never described to the model. It does not attempt the tool
and then narrate working around the failure, because it never knew the tool
existed.

## When a tool fails

Two kinds of failure, and the difference determines whether your turn survives.

**A tool failed on its own terms.** The tool ran and did not succeed, or the
model called a tool that does not exist, or the arguments failed schema
validation. All three are things the model can recover from, so they arrive as a
`tool/result` event carrying `ToolResult(is_error=True)` with a message the model
can act on. Nothing is raised, and the turn continues.

```python
async def open_ticket(args: Mapping[str, object]) -> ToolResult:
    try:
        ticket_id = await tracker.create(title=args["title"])
    except TrackerUnavailable:
        return ToolResult(content="Issue tracker is down. Try again later.",
                          is_error=True)
    return ToolResult(content=f"Opened {ticket_id}")
```

**A handler raised.** An unhandled exception is not representable as a tool
result, so it surfaces as `tool/failed`.

The practical rule: catch what you expect and return `is_error=True` so the model
can adapt. Let genuinely unexpected exceptions propagate.

## Trust

A host tool handler runs in your process, with your imports and your credentials.
The agent does not sandbox it and does not attempt to.

Tool arguments come from the model, which means they are untrusted input in the
same way user input is. Validate them in the handler. `input_schema` shapes what
the model is likely to send, but it is guidance to the model, not a security
boundary.

To gate calls before they run, use [Approvals](approvals.md).

## Errors

- **`tool/failed`** a handler raised an unhandled exception. Not used for unknown
  tool names or schema failures, which surface as `ToolResult(is_error=True)`.
- **`tool/denied`** an approval handler denied the call. Emitted as a recoverable
  error alongside a `ToolResult(is_error=True)`, never raised.
- **`tool/approval_timeout`** the approval handler did not respond in time. Also
  recoverable and never raised.

See [Errors](errors.md) for the full registry.
