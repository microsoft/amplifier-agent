# Python names and idioms

Every binding presents the same agreement in a different language. This page is the
mapping, so equivalence can be checked rather than assumed.

Sameness lives in shape and behavior, not spelling. A behavior you can observe here and
not in another binding is a defect here.

## Operations

```
create_agent                       amplifier_agent.create_agent
agent.create_session               Agent.create_session
agent.resume_session               Agent.resume_session
agent.list_sessions                Agent.list_sessions
agent.delete_session               Agent.delete_session
agent.close                        Agent.close
session.info                       Session.info
session.run                        Session.run
session.start_turn                 Session.start_turn
session.fork                       Session.fork
session.history                    Session.history
session.close                      Session.close
turn.info                          Turn.info
turn.events                        Turn.events
turn.cancel                        Turn.cancel
contract_version                   amplifier_agent.contract_version
```

## Records

```
AgentOptions       AgentOptions
TurnInput          TurnInput
TurnResult         TurnResult
ContentPart        ContentPart          union alias; TextPart is its only member
SessionRecord      SessionRecord
Event              Event
Usage              Usage
UsageEntry         UsageEntry
```

Options with no contract record of their own, because each binding shapes its own
argument objects: `SessionOptions`, `Tool`, `McpServer`, `ApprovalRequest`,
`ApprovalResponse`.

## Event types and error codes are strings, unchanged

```
event.type == "turn_started"
err.code  == "session_in_use"
```

`type` is the registry name, never a Python class name. Codes are the registered
spelling. Neither is translated, so a renderer or a log query written against
[events](../concepts/events.md) works here without a lookup table.

## Field names stay as written

Envelope and payload fields keep their contract spelling, including `session_id`,
`turn_id`, `call_id`, and `request_id`. Python spells those the same way, so nothing is
converted, and unknown owned extension fields survive untouched.

## Async everywhere

Every operation that can do work is a coroutine.

```python
agent   = await create_agent(options)
session = await agent.create_session()
result  = await session.run(input)
```

`Turn.events()` is an async iterator with a single consumer.

```python
async for event in turn.events():
    ...
```

`Agent` and `Session` are async context managers. `async with` is `close()` in a shape
Python already knows.

## Cancellation

```python
await turn.cancel()
```

Idempotent, and it reaches work already running. Cancelling a `run` through
`asyncio.CancelledError` does not: that abandons your side of the call while the turn
keeps going. Use `cancel()`.

## Errors

One exception type, carrying the whole record.

```python
class AgentError(Exception):
    code: str
    category: str
    message: str
    remedy: str
    retryable: bool
    correlation_id: str | None
    details: dict | None
```

`ToolFailed` and `ToolOutcomeUnknown` are how a handler reports its own resolution. They
are the only two exceptions this library asks you to raise.

## Decimals

`cost` values are `decimal.Decimal`, never `float`. Money never goes through binary
floating point.

## No prompt shorthand

`run` and `start_turn` take a `TurnInput`. There is no string overload.

```python
await session.run(TurnInput(content=[TextPart("Do the thing.")]))
```

A convenience invented in one binding is a difference between bindings, and differences
between bindings are what make the library expensive to trust.
