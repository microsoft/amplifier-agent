# Quickstart

Build an agent, give it a tool, and watch it work.

## Install and set a credential

```bash
uv add git+https://github.com/microsoft/amplifier-agent
```

Set a credential for whichever provider you plan to use:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

Every supported provider works the same way, and the examples below name the one
they use. [Providers](06-providers/index.md) covers the full list and how
credentials are resolved.

## Your first agent

```python
import asyncio
from amplifier_agent import AgentConfig, ProviderConfig, create_agent


async def main():
    agent = await create_agent(
        AgentConfig(
            provider=ProviderConfig(name="anthropic", model="claude-sonnet-5"),
        )
    )
    async with agent:
        session = await agent.create_session()
        result = await session.run("What files are in this directory?")
        print(result.reply)


asyncio.run(main())
```

Save that as `main.py` and run it:

```bash
uv run main.py
```

`uv run` executes the script against your project's dependencies, so there is no
virtual environment to activate. It also checks that the environment matches your
lockfile before every run.

The agent already has tools. It reads the directory with its built-in filesystem
tool and answers from what it found, without you wiring anything up.

`create_agent` is where validation happens. Constructing an `AgentConfig` is
inert, so a bad provider name or a missing credential surfaces here rather than
at import time.

## Give it your own tool

Any function becomes a tool. Describe it, hand the agent a JSON schema for its
arguments, and return whatever the model should see.

```python
from amplifier_agent import HostTool, ToolResult, ToolsConfig


async def open_ticket(args: dict) -> ToolResult:
    ticket_id = tracker.create(title=args["title"], body=args.get("body", ""))
    return ToolResult(content=f"Opened {ticket_id}")


agent = await create_agent(
    AgentConfig(
        provider=ProviderConfig(name="anthropic", model="claude-sonnet-5"),
        instructions="File a ticket whenever you find a bug you cannot fix.",
        tools=ToolsConfig(
            host_tools=[
                HostTool(
                    name="open_ticket",
                    description="File a bug report in the issue tracker.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                    handler=open_ticket,
                )
            ]
        ),
    )
)
```

The model sees `open_ticket` alongside the built-in tools and cannot tell the
difference. Your handler runs in your process, with your imports and your
credentials.

## Watch it work

`run` waits for the final reply. `stream` yields events as they happen, which is
what you want behind any interface a person is looking at.

```python
from amplifier_agent import MessageDelta, ToolCall, ToolResultEvent

async for event in session.stream("Refactor the parser and run the tests."):
    match event:
        case MessageDelta(text=text):
            print(text, end="", flush=True)
        case ToolCall(name=name, arguments=args):
            print(f"\n-> {name}({args})")
        case ToolResultEvent(result=ToolResult(is_error=True, content=content)):
            print(f"\n!! {content}")
```

Both paths run the same turn. `run` is `stream` consumed to completion.

## Approve what it does

Without an approval handler, tool calls that need approval are denied. Supply one
and you decide, call by call.

```python
from amplifier_agent import ApprovalRequest, ApprovalResponse


async def approve(request: ApprovalRequest) -> ApprovalResponse:
    print(f"{request.tool_name}: {request.arguments}")
    answer = input("allow? [y/N] ")
    if answer.lower() == "y":
        return ApprovalResponse(action="allow")
    return ApprovalResponse(action="deny", reason="operator declined")


agent = await create_agent(
    AgentConfig(
        provider=ProviderConfig(name="anthropic", model="claude-sonnet-5"),
        approvals=approve,
    )
)
```

Denying fails that one call and lets the turn continue. The agent sees the
failure and works around it. To stop the whole turn, return
`ApprovalResponse(action="cancel")`.

## Keep the conversation going

A session holds its history. Consecutive turns on the same session see each
other.

```python
session = await agent.create_session()

await session.run("Read src/parser.py and summarize it.")
await session.run("Now write tests for the third function you described.")
```

Sessions persist, so you can come back later:

```python
session = await agent.create_session()
session_id = session.id
# ... a day passes, a new process starts ...
session = await agent.resume_session(session_id)
```

Forking gives you a copy that shares the history up to that point and diverges
from there, which is how you try two approaches without losing the first.

```python
alternative = await session.fork()
```

## Where to go next

- Every configuration field, in [Configuration](03-configuration.md).
- Recording sessions and shipping them somewhere you can query, in
  [Context Intelligence](04-context-intelligence.md).
- Credentials and model selection per provider, in
  [Providers](06-providers/index.md).
- The complete surface, one page per area, in [Interface](05-interface/index.md).
