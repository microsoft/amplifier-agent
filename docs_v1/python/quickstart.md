# Python quickstart

Assumes [install](../install.md) and a provider credential in your environment.

## One turn

```python
import asyncio
from amplifier_agent import create_agent, AgentOptions, TurnInput, TextPart

async def main():
    async with await create_agent(AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
    )) as agent:
        session = await agent.create_session()
        result = await session.run(TurnInput(content=[TextPart("Say hello.")]))
        print(result.state, result.content[0].text)

asyncio.run(main())
```

`create_agent` gives you a ready agent or raises. `state` is `success`, `failure`,
`rejected`, or `cancelled`. See [turns](../concepts/turns.md).

## Watching the work

`run` waits. `start_turn` lets you watch the same turn happen.

```python
turn = await session.start_turn(TurnInput(content=[TextPart("Summarize CHANGELOG.md.")]))

async for event in turn.events():
    if event.type == "output_delta":
        for part in event.payload.content:
            print(part.text, end="", flush=True)
    elif event.type == "tool_call":
        print(f"\n[{event.payload.call.source}] {event.payload.call.name}")
    elif event.type == "terminal":
        result = event.payload
```

Appending every `output_delta` reconstructs `result.content` exactly. The stream has one
consumer; asking twice fails `stream_already_consumed`. All eleven event types are in
[events](../concepts/events.md).

## A tool your process runs

```python
from pathlib import Path
from amplifier_agent import Tool, ToolFailed

async def read_file(arguments):
    path = Path(arguments["path"])
    if not path.is_file():
        raise ToolFailed(f"{path} is not a file")
    return path.read_text()

agent = await create_agent(AgentOptions(
    provider="anthropic",
    model="claude-sonnet-5",
    tools=[Tool(
        name="read_file",
        description="Read a UTF-8 text file from disk.",
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=read_file,
    )],
))
```

Your handler runs in your process and nowhere else. Returning resolves the call
`completed`, `ToolFailed` resolves it `failed`, and `ToolOutcomeUnknown` resolves it
`unknown` when you genuinely cannot tell whether the effect landed. See
[tools](../concepts/tools.md).

## Approving effects

```python
from amplifier_agent import ApprovalResponse

async def approve(request):
    print(f"{request.name}: {request.summary}")
    return ApprovalResponse(decision="allow" if input("[y/N] ") == "y" else "deny")

agent = await create_agent(AgentOptions(..., approvals=approve))
```

Without a handler, pass `approvals="allow"` or `approvals="deny"` and the decision is made
before the turn starts. With neither, a consequential action fails
`approval_unavailable` rather than proceeding. See
[approvals](../concepts/approvals.md).

## Coming back later

```python
session = await agent.create_session(SessionOptions(session_id="ticket-4417"))
await session.run(TurnInput(content=[TextPart("Start on the login bug.")]))
await session.close()

# another process, another day
session = await agent.resume_session("ticket-4417")
await session.run(TurnInput(content=[TextPart("What did you find?")]))
```

Sessions are durable by default and resume from the local transcript alone. Creating an
id that exists fails `already_exists`, and resuming an unknown one fails `not_found`. See
[sessions](../concepts/sessions.md).

## Failures

```python
from amplifier_agent import AgentError

try:
    session = await agent.resume_session("ticket-4417")
except AgentError as err:
    print(err.code, err.remedy)
```

`remedy` is always present and always actionable. Failures raised before the stream
exists surface at the method; failures after it exists arrive in `terminal`. See
[errors](../concepts/errors.md).

## Next

```
names.md       what each contract name is called here
reference.md   every signature
../concepts/   what any of it means
```
