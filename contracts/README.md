# Amplifier Agent Contracts

Amplifier Agent is a library you embed in your application. These are the contracts
for its surface: what you can build against, and what we keep the right to change
underneath you.

## The pieces

Three words carry everything here.

```
  your application ---> binding ---,
                                    +---> engine
  your HTTP client ---> face    ---'
```

**Binding.** The library you install and call, one per language. This is the whole of
what you build against, and it is what these contracts freeze.

**Engine.** What actually runs the agent behind the binding. It is ours. You never
call it, name it, or learn what it is written in. We intend to replace it, and that
replacement is not supposed to be an event in your life.

**Face.** A network endpoint that projects part of the binding's surface, for callers
who cannot embed a library. A face carries less than a binding does and says so.

Authority runs one direction, and only one:

```
  contract  ->  binding  ->  engine

  the contract defines the surface
  each binding presents it
  the engine has to satisfy it
```

No binding is the reference implementation, including the one written in the engine's
own language. The engine never defines the surface by whatever it happens to expose.
Where an implementation and a contract disagree, the implementation is what is wrong.

The boundary between binding and engine is internal plumbing. What that plumbing must be
able to do is pinned in [`engine-seam.v1.md`](engine-seam.v1.md); how it does any of it
is pinned nowhere, on purpose, and may differ from one binding to the next.

### A library, not a command line

There is no `amplifier-agent` command for you to script against, and there will not
be one. A command line good enough to depend on becomes the surface everyone
integrates against, and argv cannot evolve the way a typed interface can. Any command
line in this repo is a development aid, and not a supported surface. The engine is not
reached by one either: [`engine-seam.v1.md`](engine-seam.v1.md) refuses to name argv as
a mechanism at all.

## Start here

Find what you are doing, read the one contract:

- **Embedding the agent in an application.** [`agent-interface.v1.md`](agent-interface.v1.md)
  The whole callable surface: agents, sessions, turns, tools, approvals, errors.
  Every other contract refines or projects this one.

- **Rendering, logging, or relaying a turn.** [`turn-events.v1.md`](turn-events.v1.md)
  The closed event vocabulary, its envelope, and its ordering laws. A renderer
  written once against this is correct against every binding.

- **Adding or maintaining a language binding.** [`language-binding.v1.md`](language-binding.v1.md)
  How a binding presents the interface in a new language without inventing semantics
  of its own.

- **Configuring a host environment.** [`host-config.v1.md`](host-config.v1.md)
  The knobs a host, meaning the application or environment running the agent, may set
  outside code, and how they resolve against the options passed in code.

- **Pointing a chat-completions client at an agent.** [`http-face.v1.md`](http-face.v1.md)
  One projection onto an OpenAI-compatible endpoint, and the capabilities that shape
  cannot carry.

- **Wiring a binding to an engine, or replacing one.**
  [`engine-seam.v1.md`](engine-seam.v1.md)
  What the connection must be able to do, and deliberately nothing about how. Internal:
  no caller builds against it.

## Contract, documentation, vision

Three kinds of writing, with three different rules:

- A **contract**, in this directory, pins a surface that callers depend on. It moves
  only through the governance below.
- **Documentation** explains how to use that surface. It is rewritten freely,
  whenever a better explanation exists.
- [VISION.md](VISION.md) says where Amplifier Agent is headed. It pins nothing, so
  nothing in it is something you can build against.

The vision makes one demand of the contracts: none of them may pin something that
puts the vision out of reach. Freezing a Python type into a public signature would do
exactly that, because replacing the engine would then break every caller. That is why
the bindings are contracted now, rather than on the day the engine needs replacing.

Confusing a contract with documentation is how a surface gets frozen, or moved, by
accident.

## The freeze bar

A contract freezes when all four of these exist, and not before:

1. The spec, in this directory.
2. A conformance kit with discriminating good and broken fixtures. Passing the kit is
   the definition of compatible.
3. A real implementation passing it. For `agent-interface` and `turn-events`: Python
   plus one independently implemented non-Python binding.
4. A worked example a stranger can follow.

A contract governing an internal boundary rather than a caller-facing surface satisfies
item 2 through the kits of the contracts it serves, named in its own Conformance section.
Giving such a boundary a private harness would re-freeze the mechanism it exists to keep
free, which is the opposite of why it was written.

The owner stamps a freeze by ratifying a dated changelog entry in the contract itself.
A contract carrying no such entry is not frozen, whatever its body says it pins. Read
the changelog, not the prose, to know where a contract stands.

Conformance has three parts:

- **Runtime black-box scenarios.** Public APIs only, against the **stub provider**: a
  test double that replays scripted responses in place of a real model, so every
  assertion is a property of the engine rather than of the model. It is part of the
  kit, not of any contract surface.
- **Static surface lint.** Denylist scan and record shapes.
- **Replacement acceptance.** The same scenarios, a replaced engine, new sessions.

## Versioning

- Each contract versions independently (`agent-interface/1`, `turn-events/1`, and so
  on). None of these is a package or release version. The connection between a binding
  and the engine exposes no protocol version of its own; what gets compared across it
  are these public contract versions.
- Within a major version, changes are **additive only**. Nothing is removed, renamed,
  re-typed, or re-defaulted. Making anything newly mandatory is breaking, unless
  evidence shows existing consumers absorb it.
- Consumers preserve what they do not recognize. New optional fields and new owned
  extension types will appear within a major version, so read defensively. A new
  registered event type is a contract change, never something that arrives on its own.
- A breaking change is a new major version in a new file, dual-served through a
  stated window.
- v1 freezes the minimum. Everything else sits in `Backlogged`, with a named trigger,
  or in `Reserved`. A trigger makes an item eligible for a CANDIDATE amendment. It
  promotes nothing by itself, and whether the resulting change fits inside the major
  version or needs a new one is decided at ratification.

## Governance

**Amend the governing document, file work items against the amendment, then execute.**

Code ahead of its document is drift. Amend deliberately or revert. Never edit the
document to describe what already happened.

- Amendments are CANDIDATE artifacts, paid for in evidence: a break, a cost, a
  platform that will not do it. Only the owner ratifies, in so many words.
- An implementation that cannot satisfy a contract is a defect in the implementation.
  Amending the contract to match is an owner decision paid for in evidence, never the
  default resolution and never a side effect of writing down what shipped.
- Convergent changes, where code catches up to the contract, are free. Divergent ones
  buy an amendment first or land as debt.
- The body and its dated changelog entry move in the same pass.
- Nothing here tracks progress. That is the issue queue and the git history.

## House rules, inherited by every contract

- **Fail loud.** Every error names the failure and the remedy. No quiet degradation,
  no partial answer sold as a whole one, no plausible stand-in values.
- **Closed vocabularies.** Config keys, event types, and error codes are registered
  sets. Extensions use owned reverse-domain keys. Unregistered unqualified members
  are refused by name.
- **Internals stay internal.** Contracts describe what a caller relies on, never how
  we deliver it. The how is what we keep the right to replace.
- **Providers hold nothing load-bearing.** Conversation history lives with the caller.
  Server-side retention is off unless a host opts in, and no provider-side state is
  ever what a session depends on.
- **Non-goals are contracts.** `Excluded` lists have no promotion path. Entries that
  name a thing are enforced by static lint; entries that name a behavior are enforced
  at amendment review. Building one back in is a regression either way.
