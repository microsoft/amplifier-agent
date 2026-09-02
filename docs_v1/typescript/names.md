# TypeScript names and idioms

Every binding presents the same agreement in a different language. This page is the
mapping, so equivalence can be checked rather than assumed.

Sameness lives in shape and behavior, not spelling. A behavior you can observe here and
not in another binding is a defect here.

## Operations

```
create_agent                       createAgent
agent.create_session               Agent.createSession
agent.resume_session               Agent.resumeSession
agent.list_sessions                Agent.listSessions
agent.delete_session               Agent.deleteSession
agent.close                        Agent.close
session.info                       Session.info
session.run                        Session.run
session.start_turn                 Session.startTurn
session.fork                       Session.fork
session.history                    Session.history
session.close                      Session.close
turn.info                          Turn.info
turn.events                        Turn.events
turn.cancel                        Turn.cancel
contract_version                   contractVersion
```

## Records

```
AgentOptions       AgentOptions
TurnInput          TurnInput
TurnResult         TurnResult
ContentPart        ContentPart          union; TextPart is its only member
SessionRecord      SessionRecord
Event              Event
Usage              Usage
UsageEntry         UsageEntry
```

Options with no contract record of their own, because each binding shapes its own
argument objects: `SessionOptions`, `Tool`, `McpServer`, `ApprovalRequest`,
`ApprovalResponse`.

## Which fields are camelCase

One rule covers it:

```
what you construct   camelCase        mcpServers, inputSchema, sessionId
what you receive     as contracted    session_id, call_id, tokens_in
```

Anything arriving from the agent keeps its contract spelling. That is not stubbornness:
payload fields grow additively and carry owned extension keys, and case-converting a
record you do not fully know how to read is how extension fields get mangled or dropped.

So `AgentOptions.mcpServers` is camelCase and `event.payload.call.call_id` is not.

## Event types and error codes are strings, unchanged

```ts
event.type === "turn_started"
err.code   === "session_in_use"
```

`type` is the registry name, never a class name. Codes are the registered spelling.
Neither is translated, so a renderer or a log query written against
[events](../concepts/events.md) works here without a lookup table.

## Promises and async iteration

Every operation that can do work returns a `Promise`.

```ts
const agent = await createAgent(options);
const session = await agent.createSession();
const result = await session.run(input);
```

`Turn.events()` returns an `AsyncIterable` with a single consumer.

```ts
for await (const event of turn.events()) { }
```

`Agent` and `Session` implement `Symbol.asyncDispose`, so `await using` closes them where
your runtime supports it. Otherwise call `close()`.

## Cancellation

```ts
await turn.cancel();
```

Idempotent, and it reaches work already running. Aborting your own `await` with an
`AbortSignal` does not: that abandons your side of the call while the turn keeps going.
Use `cancel()`.

## Errors

One error class, carrying the whole record.

```ts
class AgentError extends Error {
  code: string;
  category: string;
  remedy: string;
  retryable: boolean;
  correlation_id?: string;
  details?: unknown;
}
```

`ToolFailed` and `ToolOutcomeUnknown` are how a handler reports its own resolution. They
are the only two errors this library asks you to throw.

## Decimals

`cost` values are strings, not numbers.

```ts
usage.entries[0].cost   // { USD: "0.0142" }
```

TypeScript's `number` is a binary float and cannot hold a decimal amount faithfully, so
the string is the most accurate native representation available. Parse it with whatever
decimal library you already use. Do not call `Number()` on money.

## No prompt shorthand

`run` and `startTurn` take a `TurnInput`. There is no string overload.

```ts
await session.run({ content: [{ type: "text", text: "Do the thing." }] });
```

A convenience invented in one binding is a difference between bindings, and differences
between bindings are what make the library expensive to trust.
