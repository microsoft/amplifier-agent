# Python quickstart

Assumes [install](../install.md) and a provider credential in your environment.

## One turn

```python
import asyncio
from amplifier_agent import create_agent, AgentOptions, SessionOptions, TurnInput, TextPart

async def main():
    async with await create_agent(AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
    )) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        result = await session.run(TurnInput(content=[TextPart("Say hello.")]))
        print(result.state, "".join(part.text for part in result.content or []))
        if result.error is not None:
            print(result.error.code, result.error.message, result.error.remedy)

asyncio.run(main())
```

`create_agent` gives you a ready agent or raises. `state` is `success`, `failure`,
`rejected`, or `cancelled`. See [turns](../concepts/turns.md).
This example uses an ephemeral conversation, whose lifetime ends at close.
Omit `provider` or `model` to use its resolved [host configuration](../configuration.md).

The following snippets run inside an async function. Session examples assume an open
agent or session, and belong before the enclosing `async with` exits.

## Watching the work

`run` waits. `start_turn` lets you watch the same turn happen.

```python
turn = await session.start_turn(TurnInput(content=[TextPart("Explain what an agent session is.")]))

async for event in turn.events():
    if event.type == "output_delta":
        for part in event.payload.content:
            print(part.text, end="", flush=True)
    elif event.type == "tool_call":
        print(f"\n[{event.payload.call.source}] {event.payload.call.name}")
    elif event.type == "terminal":
        result = event.payload
        if result.error is not None:
            print(f"\n{result.error.code}: {result.error.remedy}")
```

Appending every `output_delta` reconstructs `result.content` exactly. The stream has one
consumer; asking twice fails `stream_already_consumed`. All eleven event types are in
[events](../concepts/events.md).
To stop early, call `await turn.cancel()` and keep consuming through `terminal`.
Leaving the loop alone does not cancel the turn.

## A tool your process runs

```python
from pathlib import Path
from amplifier_agent import Tool, ToolFailed

async def read_note(arguments, context):
    print(f"Reading file for call {context.call_id}")
    path = Path(arguments["path"])
    try:
        return await asyncio.to_thread(path.read_text, encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ToolFailed(f"Cannot read {path}: {exc}") from exc

read_note_tool = Tool(
    name="read_note",
    description="Read a UTF-8 note from disk.",
    input_schema={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=read_note,
)
```

Pass `tools=[read_note_tool]` in `AgentOptions` when constructing the agent, together
with an approval policy from the next example. Tool names must be distinct from
[built-in tools](../concepts/tools.md#built-in-tools-and-skills).

Your handler runs in your process and nowhere else. Returning resolves the call
`completed`, `ToolFailed` resolves it `failed`, and `ToolOutcomeUnknown` resolves it
`unknown` when you genuinely cannot tell whether the effect landed. See
[tools](../concepts/tools.md).

## Approving effects

```python
from amplifier_agent import ApprovalResponse

async def approve(request):
    print(f"{request.name}: {request.summary}")
    answer = await asyncio.to_thread(input, "[y/N] ")
    return ApprovalResponse(decision="allow" if answer == "y" else "deny")
```

Pass `approvals=approve` in `AgentOptions` when constructing the agent. This handler
needs an interactive terminal; a server application can ask through its own interface.

Without a handler, pass `approvals="allow"` or `approvals="deny"` and the decision is made
before the turn starts. With neither, a consequential action fails
`approval_unavailable` rather than proceeding. See
[approvals](../concepts/approvals.md).

## Supplying a conversation

```python
from amplifier_agent import ConversationMessage, SessionOptions

async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
    result = await session.run(TurnInput(
        content=[],
        history=[
            ConversationMessage(role="user", content=[TextPart("My name is Ada.")]),
            ConversationMessage(role="assistant", content=[TextPart("Hello, Ada.")]),
            ConversationMessage(role="user", content=[TextPart("What is my name?")]),
        ],
    ))
    print(result.state, result.content)
```

The complete conversation goes in `history`; empty `content` adds no user message.
Supply history only before an ephemeral session has accepted a turn and when it has no
inherited conversation. See [turns](../concepts/turns.md#supplying-a-conversation).

## Coming back later

```python
session = await agent.create_session(SessionOptions(session_id="ticket-4417"))
await session.run(TurnInput(content=[TextPart("Start on the login bug.")]))
await session.close()

# another process, another day
session = await agent.resume_session("ticket-4417")
async with session:
    result = await session.run(TurnInput(content=[TextPart("What did you find?")]))
    print(result.state, result.content)
```

Sessions are durable by default and resume from the local transcript alone. Creating an
id that exists fails `already_exists`, and resuming an unknown one fails `not_found`. See
[sessions](../concepts/sessions.md).
Use the same `storage` root and configured `workspace` in both processes, and
reconstruct the agent's credentials, tools, and approval policy. Closing an agent
closes its sessions without deleting durable transcripts.

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
`run` returns those terminal failures as `TurnResult`; check `result.state` and
`result.error` as well as catching `AgentError`.

## Next

```
names.md       what each contract name is called here
reference.md   every signature
../concepts/   what any of it means
```
