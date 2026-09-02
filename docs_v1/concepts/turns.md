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
TurnInput    { content: [ContentPart...], model? }
ContentPart  { type: "text", text }
```

`ContentPart.type` is a closed set holding only `"text"`.

`model` refines the ceiling for this turn alone. See [models](models.md).

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
