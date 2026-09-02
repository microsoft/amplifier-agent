# Language Binding Contract v1 (FROZEN 2026-09-02)

**Who builds against this:** us, whenever we add or maintain a binding. Callers read
the binding itself, not this.

**What it freezes:** that every binding is the same agreement in a different language,
so the engine can be replaced without a caller noticing.

Applications and adapters talk to a binding or a face, never past one:

```text
  your application ---> binding ---,
                                    +---> engine
  your HTTP client ---> face    ---'

  we own the binding and the face; the caller owns everything to the left
```

The engine is where the work happens, and it is the part we intend to replace. That
swap stays affordable only while nothing on the caller's side has reached through a
binding to touch it.

## What makes bindings the same

**No binding is the reference.** There is no first binding that others are ported
from, and the language the engine happens to be written in earns no special status
(section 5). A binding copied from another inherits its accidents along with its
shape, and those accidents are what would make the engine expensive to replace later.

Sameness comes from three things instead:

- **The contract.** [`agent-interface.v1`](agent-interface.v1.md) and
  [`turn-events.v1`](turn-events.v1.md) are the source of truth. Every binding is
  written against those documents, and a disagreement between two bindings is settled
  by reading them, not by deferring to the older binding.
- **A published mapping.** Each binding states which local name carries which contract
  name (section 3), so equivalence can be checked rather than argued.
- **One shared conformance suite.** Every binding runs the same scenarios and must
  produce the same results, the same event order, and the same failure codes. This is
  the only mechanical guarantee of sameness. It is why the freeze bar wants two
  independently implemented bindings passing: a single binding passing a suite written
  alongside it proves very little.

The engine sits downstream of all of this. It does not define the shape, it has to
satisfy it. How a given binding reaches the engine is plumbing and may differ from one
binding to the next; what that plumbing must be capable of is
[`engine-seam.v1`](engine-seam.v1.md).

## 1. A binding presents the interface, whole

A binding presents [`agent-interface.v1`](agent-interface.v1.md) in full. Nothing is
skipped as inconvenient, and nothing beyond it is offered as a bonus.

## 2. A binding adds no semantics

It maps calls, translates types, and reports errors. That is all.

No retry, no cache, no batching, no reordering, no event filtering, no invented
defaults, and no deciding on the caller's behalf.

A behavior observable in one binding and not another is a defect in the one that
invented it.

## 3. Idioms, with a published mapping

Naming, async style, cancellation, errors, iteration, and packaging follow the
language. Sameness lives in shape and semantics, not spelling.

Each binding publishes its contract-name to local-name mapping, so equivalence is
checked rather than assumed.

## 4. The transport is invisible and disposable

In-process, subprocess, socket, or FFI: none of it appears in the interface.

Literal exclusions: argv, ports, envelopes, stream framing, executable paths, and
environment variables a caller must set.

Changing transport is not a contract change, provided the new one still satisfies
[`engine-seam.v1`](engine-seam.v1.md), which pins what any connection must be able to
do and nothing about how.

## 5. The engine's own language gets a binding like every other

Same shape, same restrictions, no direct reach. The engine is written in some
language, and callers in that language get a binding rather than a way around it.

That binding may be a thin pass-through on the day it is written. It still has to
exist, because it is the layer that absorbs the plumbing when the engine is replaced.
Without it, callers in that one language are coupled straight to the engine, and the
engine's current shape quietly becomes the contract.

This is the rule most easily rationalized away, and the one whose loss makes the rest
pointless.

## 6. No caller-facing command line is shipped

Amplifier Agent is a library. Nothing here is designed around a command line, and no
binding gains one.

A CLI good enough to depend on becomes the surface everyone integrates against, and
argv cannot evolve the way a typed interface can. Shipping one would freeze it by
usage rather than by decision.

Any CLI in this repo exists for development and verification, and is not a supported
surface. A caller who wants one writes it over a binding, in their own repo.

## 7. A binding declares its contract versions

Readable without invoking anything. A mismatched engine is refused by name
(`contract_version_mismatch`).

This contract is `language-binding/1`, independent of the other contracts and of every
package version. Changes within the major version are additive only.

## 8. Bindings move together

An interface change or an event change lands in every binding in one change set. A
binding left behind is not lagging, it is broken.

## 9. Lossless carriage

Ids, strict JSON, content order, exact integers, decimal strings, every registered event,
and every valid owned extension arrive intact and in order.

Errors arrive in the language's idiom with the full record inspectable. Flattening an
error to a string destroys the remedy, the only part a caller can act on.

## Invariants

1. **No escape hatch** to the engine, however well named.
2. **No binding-only feature.** Promote it into the interface, or remove it.
3. **Errors and events survive translation**, complete and in order.
4. **A binding does only what section 2 permits.** Every other line is engine logic
   that will be rewritten once per language, and again when the engine moves.

## Bindings, faces, and adapters

```text
binding   presents the interface in a language; governed here; one per language
face      projects part of the interface onto a protocol; has its own contract
adapter   caller-written, over a binding, in the caller's repo; ungoverned here,
          and may not add agent semantics (interface invariants)
```

Need approvals, caller tools, or the full event stream? Embed a binding. A face
growing toward those is a second implementation of the interface, in a protocol chosen
for a different reason.

Which languages get bindings is an issue-queue decision. Whatever set exists satisfies
every rule above.

## Excluded

No promotion path:

- A binding-declared capability set. Bindings are equivalent by rule, not by
  negotiation.
- Per-binding configuration.

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **A code-generation contract.** A binding demonstrably cannot be written correctly
  by hand against the published mapping.
- **A sync surface as contract rather than idiom.** Two bindings need identical sync
  semantics that a pure async-veneer rule cannot express.

## Conformance

One shared suite, executed by every binding against the stub provider:

- Every operation and event type resolves through the published mapping, with no
  dangling entries
- One scripted turn yields equal results and an identical event order across bindings,
  for run, resume, and fork
- The same induced failure yields the same code and remedy everywhere
- A mismatched engine is refused by name
- Surface enumeration finds no name outside the interface, and none of the transport
  artifacts named in section 4

Freeze requires Python plus one independently implemented non-Python binding passing.

## Reserved

Not frozen, and not yet decided:

- Whether the shared suite is fixtures-per-binding or an external driver
- Binding release cadence relative to the engine

## Changelog

Dated, owner-ratified amendments only.

- 2026-09-02: v1 FROZEN by owner ratification. Freeze bar at stamp time: the
  spec exists.

