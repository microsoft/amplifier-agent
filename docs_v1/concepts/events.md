# Events

A turn is visible only through its events. The vocabulary is closed and identical in
every language, so a renderer written once is correct everywhere.

## Envelope

```
{
  contract_version: "turn-events/1",
  session_id,
  turn_id,
  sequence,
  at?,
  type,
  payload
}
```

`sequence` starts at 1 per turn and increments by exactly 1. That is what makes loss and
reordering detectable rather than something you hope did not happen.

Event identity is `(session_id, turn_id, sequence)`. `at`, when present, is RFC 3339 UTC.
`type` is the registry name below, never a class name from your language.

## The eleven types

```
turn_started       continuation: "fresh" | "resumed"     first, once
                   primary_actual: { provider, model }

output_delta       content: [ContentPart...]             reply, incrementally
reasoning_delta    text                                  reasoning, incrementally
reasoning_final    text                                  reasoning, complete

tool_call          call                                  a tool is about to run
tool_result        resolution                            that call's one resolution
approval_request   request                               an effect awaits you
approval_decision  resolution                            the one correlated answer

progress           strict JSON                           non-terminal progress
usage              snapshot                              cumulative, full replacement
terminal           state, content?, error?, usage?       last, once
```

`ContentPart` and the `terminal` payload are defined in [turns](turns.md). The `call` and
`resolution` shapes are in [tools](tools.md) and [approvals](approvals.md). The `usage`
snapshot is in [usage](usage.md).

`progress` never implies success.

There is no unqualified type outside these eleven.

## Ordering

**Bracketing.** `turn_started` first, exactly once. `terminal` last, exactly once,
including on failure and cancellation. Nothing follows `terminal`. Silence is never a
terminal state.

**Pairing.** Every `tool_call` has exactly one later `tool_result` carrying the same
`call_id`, before `terminal`. Every `approval_request` has exactly one
`approval_decision` carrying the same `request_id`. No result or decision arrives without
its request first. An accepted cancellation drains the pairs before terminating.

**Reconstruction.** Appending `output_delta` parts in order reconstructs
`terminal.content` exactly, and `reasoning_final` equals the run of `reasoning_delta`
before it. Rendering deltas or finals is a choice, not a gamble.

**Usage placement.** If any attributable model work happened, at least one `usage` event
appears. The last one follows all work, precedes `terminal`, and is the final cumulative
snapshot. `terminal.usage`, when present, equals it exactly.

## Extensions

Payload fields grow additively. Existing fields keep their name, type, and meaning.

Anything not in the registry uses an owned reverse-domain key, such as
`org.example.trace`, for both event types and fields. Those arrive verbatim. They are
never turned into a registered meaning, never read as evidence of success, and never
allowed to shadow a registered name.

Preserve what you do not recognize. New optional fields and new owned extensions will
appear within this major version.

## Recording a turn

The stream is the record. Every event carries its identity and its position, so writing
each one to your own sink gives you a complete, gap-checkable account of what the agent
did, with nothing else to configure.

For capture wired by the environment rather than by you, see
[context intelligence](../context-intelligence.md).
