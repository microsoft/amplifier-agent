# Turns

A turn is one exchange: you give the agent something to do, it works, it terminates.

## Two ways to take the same turn

```
result = session.run(input)         wait for the outcome
turn   = session.start_turn(input)  watch the work
```

Both take the same turn by the same path. `run` returns exactly the `TurnResult` that the
stream's `terminal` event carries, so choosing between them is choosing presentation, not
behavior.

```
turn.info      { session_id, turn_id }
turn.events()  ordered stream of Event, single consumer
turn.cancel()  idempotent
```

`events()` has one consumer. Asking twice fails `stream_already_consumed`.

## Input

```
TurnInput           { content: [ContentPart...], model?, history?: [ConversationMessage...] }
ConversationMessage { role: "system"|"developer"|"user"|"assistant",
                      content: [ContentPart...] }
ContentPart         { type: "text", text }
```

`ContentPart.type` is a closed set holding only `"text"`.

`model` refines the ceiling for this turn alone. See [models](models.md).

## Supplying a conversation

`history` seeds an ephemeral session before it has accepted any turn, provided it has
no inherited conversation. Supplying it in any other session fails `invalid_input`.
Omitting it leaves ordinary turn behavior unchanged.

With `history`, nonempty `content` appends one user message after the supplied messages.
`content: []` appends nothing. `history: []` with `content: []` fails `invalid_input`.
Any supported role may be last; the agent does not require a trailing user message.

The accepted input is snapshotted, preserving message order, roles, text and content-part
boundaries. `system` and `developer` messages remain conversation content; they do not
replace configured instructions, tools or approvals.

Unsupported roles, tool/function-call structures and non-text content fail `invalid_input`.
Invalid seeded input is refused at the method before a stream exists, before a provider
request and before any effect. The refusal leaves the session unchanged, so a corrected
seed can still be its first accepted turn. Existing `closed` and `busy` errors still apply.

The first turn has `continuation: "fresh"`. Later turns and forks retain the supplied
conversation as described in [sessions](sessions.md).

## Result

```
TurnResult { state, content?, error?, usage? }
```

`TurnResult` is exactly the payload of the `terminal` event. Read one and you have read
the other.

```
success     no error
failure     carries error
rejected    carries error, from a denied approval
cancelled   carries turn_cancelled, or approval_cancelled
```

## Termination

Every turn ends with exactly one `terminal`, after all paired resolutions have drained.
That holds for failures and cancellations too.

A stream that goes quiet without a terminal is a defect. Do not build a timeout around
it.

## Cancelling

`cancel()` is idempotent. An accepted cancellation starts no new work, drains the pairs
already outstanding, and fixes the terminal to `cancelled` with `turn_cancelled`.

Work already in flight may still land. A tool that cannot say whether its effect happened
reports `unknown`, and that is passed through as [uncertainty](tools.md), never rounded
to success or failure.
