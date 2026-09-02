# Turn Events Contract v1 (FROZEN 2026-09-02)

**Who builds against this:** anything that renders, logs, records, or relays a turn.
Bindings carry this vocabulary whole, so a renderer written once is correct against
all of them. Faces carry a declared subset (section 5).

**What it freezes:** a turn is visible only through its events. This freezes the
vocabulary, the envelope, and the ordering laws. It does not freeze each payload's
field list, which grows additively.

## 1. Envelope

```text
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

`sequence` starts at 1 per turn and increments by exactly 1. It is the mechanism of
the ordering guarantee: loss and reorder are machine-detectable.

Event identity is `(session_id, turn_id, sequence)`.

`at`, when present, is RFC 3339 UTC. `type` is the stable name from the registry
below, never a language class name.

## 2. Vocabulary, closed at eleven

```text
turn_started       continuation: "fresh"|"resumed"     first, once
                   primary_actual: {provider, model}

output_delta       content: [ContentPart...]           reply content, incrementally
reasoning_delta    text                                reasoning, incrementally
reasoning_final    text                                reasoning, complete

tool_call          call (source-named)                 a tool is about to run
tool_result        resolution                          that call's one resolution
approval_request   request                             an effect awaits the caller
approval_decision  resolution                          the one correlated answer

progress           strict JSON                         non-terminal progress
usage              snapshot                            cumulative, full replacement
terminal           state, content?, error?, usage?     last, once
```

A `tool_call` names its source, one of `built-in`, `caller`, or `mcp`, which is also the
party that executes the effect. See [`agent-interface.v1`](agent-interface.v1.md)
section 6.

`ContentPart` and the `terminal` payload are defined in
[`agent-interface.v1`](agent-interface.v1.md) section 1. This contract governs when
they appear and in what order, never their field lists.

`progress` **never implies success**.

No unqualified type exists outside these eleven.

Extensions use **owned keys**, reverse-domain, such as `org.example.trace`, for both
event types and fields. Every binding preserves them verbatim. They are never
transformed into a registered meaning, never treated as evidence of success, and never
allowed to shadow a registered name.

## 3. Ordering laws

**Bracketing.** `turn_started` comes first, exactly once. `terminal` comes last,
exactly once, including on failure and on cancellation. Nothing follows `terminal`.
Silence is never a terminal state.

**Pairing.** Every `tool_call` has exactly one later `tool_result` with the same
`call_id`, before `terminal`. Every `approval_request` has exactly one
`approval_decision` with the same `request_id`. No result or decision appears without
its prior request. An accepted cancellation drains the pairs first.

**Reconstruction.** Appending `output_delta` parts in order reconstructs
`terminal.content` exactly, and `reasoning_final` equals its run of
`reasoning_delta`. Rendering deltas or finals is a safe choice, never a guess.

**Usage placement.** If any attributable model work occurred, at least one `usage`
event appears. The last one follows all work, precedes `terminal`, and is the final
cumulative snapshot. `terminal.usage`, when present, equals it exactly.

## 4. Terminal states

```text
success     omits error
failure     carries error
rejected    carries error
cancelled   carries turn_cancelled (accepted cancel)
                 or approval_cancelled (approval-driven)
```

Recoverable errors surface through `progress` or `tool_result` and the turn continues.
Unrecoverable ones ride `terminal`.

## 5. Growth and subsets

Payload fields grow additively. Existing fields keep their name, type, and meaning,
and consumers preserve what they do not recognize.

A **binding** drops nothing.

A **face** carries a subset declared in its own contract. That is a stated loss, never
a judgment call at emit time. A face never merges, splits, reorders, or silently drops
a type.

## Invariants

1. **No event names an internal.** Delegation appears as ordinary tool activity. No
   composition unit, loop component, prompt fragment, or routing decision is ever
   named.
2. **Accounting is truthful or omitted.** A counter wired to a constant teaches every
   caller to stop reading the field.
3. **`progress` never implies success**, and is never engine-authored prose a caller
   cannot act on.

## Excluded

No promotion path:

- Replay of a completed turn. The stream is live observation.
- A second freeform text channel.

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **Sub-agent lifecycle events.** Two implementations show identical host-visible
  nesting, cancellation, and accounting.
- **Binary and media content parts.** A real caller needs them, with evidence of
  lossless cross-binding representation.
- **Incremental tool-argument streaming.** A real renderer demonstrates a need that
  deltas cannot serve.

## Conformance

Against the stub provider:

- Bracket-once
- Contiguous sequence
- Pairing by id, including under cancellation
- Delta-to-final and delta-to-terminal reconstruction
- Final-usage placement
- All four terminal states reachable by fixture
- No unqualified type outside the eleven
- Unknown owned events and fields survive a binding round-trip
- One scripted turn through any two bindings yields an identical type order

Static lint: the unqualified namespace is held here, and extension keys are
ownership-qualified.

## Reserved

Not frozen, and not yet decided:

- Whether a face's wire framing is itself contract, and where that is written

Usage cadence is held in [`agent-interface.v1`](agent-interface.v1.md) Reserved, which
owns the usage record.

## Changelog

Dated, owner-ratified amendments only.

- 2026-09-02: v1 FROZEN by owner ratification. Freeze bar at stamp time: the
  spec exists.

