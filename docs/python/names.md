# Python names and idioms

This page maps contract names to Python.

## Names are unchanged

Operations keep their contract names. `create_agent`, `contract_version`,
`contract_versions`, and `BUILTIN_TOOLS` are module-level in `amplifier_agent`; the rest
are methods and properties on `Agent`, `Session`, and `Turn`.

Record and event payload names are the contract names, unchanged. Options and
callbacks with no contract record, such as `SessionOptions`, `Tool`, and
`ApprovalHandler`, are Python types. Every exported name is in [reference](reference.md).

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
agent = await create_agent(options)
session = await agent.create_session()
result = await session.run(input)
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

One exception type, [`AgentError`](reference.md#errors), carrying the whole record.

`ToolFailed` and `ToolOutcomeUnknown` are how a handler reports its own resolution. They
are the only two exceptions this library asks you to raise.

## Decimals

`cost` values are `decimal.Decimal`, never `float`. Money never goes through binary
floating point.

## Tool callbacks

Handlers receive decoded arguments and a `ToolContext` containing the correlated
`call_id` and optional `deadline`. A deadline is an aware UTC `datetime`; absence means
no deadline was supplied. The context is read-only.
A handler returns a `str` or a list of `TextPart` and `ImagePart`.

`ImagePart.data` is a base64 `str`, not `bytes`:

```python
ImagePart(media_type="image/png", data=base64.b64encode(png).decode())
```

## No prompt shorthand

`run` and `start_turn` take a `TurnInput`. There is no string overload.

```python
await session.run(TurnInput(content=[TextPart("Do the thing.")]))
```
