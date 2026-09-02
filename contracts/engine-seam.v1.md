# Engine Seam Contract v1 (FROZEN 2026-09-02)

**Who builds against this:** us, whenever we wire a binding to an engine, replace an
engine, or propose a new transport. No caller reads this, and nothing outside our own
code may depend on it.

**What it freezes:** what the connection between a binding and the engine must be able
to do. Nothing about how it does any of it.

**What defines it:** the promises in [`agent-interface.v1`](agent-interface.v1.md) and
[`turn-events.v1`](turn-events.v1.md). Every requirement below exists because something
above it is promised to a caller. A connection that cannot meet one of these is an
inadequate connection, never grounds to narrow a contract.

## 1. The mechanism is free, the capability is not

In-process, FFI, socket, daemon, RPC, or something not yet invented: none of it appears
in the interface, none of it is frozen here, and it may differ from one binding to the
next. A binding couples to the engine however serves it best, and re-couples freely when
the engine is replaced.

Changing the mechanism is not a contract change. Losing one of the capabilities below
is.

## 2. Bidirectional for the whole life of a turn

The engine can ask the caller something mid-turn and receive the answer before the turn
ends.

This is what approvals ([`agent-interface.v1`](agent-interface.v1.md) section 7) and
caller-supplied tools (section 6) are made of. A policy decided before the turn starts
is not an approval, and a tool the engine cannot call into is not a caller tool.

## 3. Events arrive live and in order

Events reach the caller as the work happens, in `sequence` order, every one of them
before the turn's result. A connection that accumulates events and delivers them at the
end satisfies nothing in [`turn-events.v1`](turn-events.v1.md) section 3, whatever order
they arrive in.

## 4. Cancellation reaches work already in flight

A cancellation crosses into a running turn, and that turn still terminates exactly once
with its pairs drained.

## 5. Failures cross as records

An error arrives whole: code, category, message, remedy, retryable, and details. A
failure never degrades into a status value the binding has to interpret, and the
connection mints no code of its own. Everything it reports is a registered code from
[`agent-interface.v1`](agent-interface.v1.md) section 9, with any mechanism detail in
`details`.

## 6. Nothing is lost in transit

Ids, strict JSON, content order, exact integers, decimal strings, every registered
event, and every valid owned extension arrive intact and in order.

## 7. Isolation holds

Independent agents do not leak into one another, and concurrent sessions do not
interfere, whatever the connection shares underneath.

## 8. A binding can establish what it is talking to

A binding can determine whether the engine satisfies the contract versions that binding
presents, and refuses by name when it does not (`contract_version_mismatch`). An engine
that cannot be reached at all is `engine_unavailable`.

How that determination is made is mechanism. The versions being compared are the public
contract versions; the connection exposes no protocol version of its own, to a caller or
to anyone else.

## 9. The connection is invisible

No caller-facing name, type, error, or artifact reveals it. Named literally: no argv, no
ports, no envelopes, no stream framing, no executable paths, and no environment variables
a caller must set.

A caller who can tell how their binding reaches the engine is looking at a defect.

## Invariants

1. **Requirements here, mechanism nowhere.** The moment this document names a transport,
   it has frozen the thing it exists to keep free.
2. **The interface sets the floor.** Every requirement traces to a promise in
   `agent-interface.v1` or `turn-events.v1`. Nothing is required here for its own sake.
3. **A limitation is never a licence.** A connection that cannot do one of these is
   unfinished. It does not shrink the contract, and it does not earn a per-binding
   capability exception (`language-binding.v1` Excluded).

## Excluded

No promotion path:

- A frozen wire format, framing, or protocol version. Freezing one buys a third-party
  drop-in-engine guarantee nobody asked for, and costs the two things we want: tighter
  coupling today, and free re-coupling when the engine is replaced.
- A caller-reachable path to the engine, however well named.
- Per-binding capability negotiation.
- A caller-facing command line, here or anywhere. Argv is a mechanism this contract
  refuses to name, and a surface `agent-interface.v1` refuses to have.

## Conformance

Verified indirectly, and deliberately so. A connection conforms when every binding over
it passes the public kits: `agent-interface`, `turn-events`, and binding parity.

That is the gate for any seam change, up to and including replacing the engine: every
binding green, in the same change set. There is no separate seam harness and no CANDIDATE
process for changing mechanism.

Replacing the engine is rewiring N owned bindings, proven by those kits. It is never
dropping in a binary certified at this seam.

## Versioning

`engine-seam/1`, independent of the other contracts and of releases. Additive only: a
new requirement may appear, and none is removed, weakened, or re-defaulted.

## Changelog

Dated, owner-ratified amendments only.

- 2026-09-02: v1 FROZEN by owner ratification. Freeze bar at stamp time: the
  spec exists.
