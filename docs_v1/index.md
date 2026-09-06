# Amplifier Agent

A library you embed in your application. Name a model, hand it tools, give it a task. It
reasons, acts, and reports back, emitting a typed event for everything it does along the
way.

The tools decide what it is for. A filesystem and a shell make it a coding agent. Your
deployment API makes it a release agent.

## Three words

```
  your Python or TypeScript application ---> binding ---> engine
  your HTTP client ---> HTTP face ---> Python binding ---> engine
```

**Binding.** The library you install and call, one per language. This is the whole of
what you build against.

**Engine.** Coordinates sessions, model requests, and approved tool work behind the
bindings. Its implementation can change without changing the contracted public API.

**Face.** A network endpoint projecting part of the binding's surface, for callers who
cannot embed a library. A face carries less than a binding does and says so.

There is no `amplifier-agent` command to script against. Build shell workflows over
a binding. The separate `amplifier-agent-face` service hosts the HTTP API.

## Pick a surface

- [Python](python/quickstart.md): embed the library with Python 3.12 or newer.
- [TypeScript](typescript/quickstart.md): embed the ESM library with Node 22 on
  Linux x86-64, including compatible WSL2 distributions.
- [HTTP](http/quickstart.md): point a chat-completions client at the service.

Bindings are equivalent. Same operations, same events, same failures, spelled the way
each language spells things. The face is deliberately narrower, and
[names what it drops](http/limits.md).

Need approvals, tools your own process executes, or the full event stream? Embed a
binding.

## Start here

Follow the [installation guide](install.md), configure your
[provider credentials](providers.md), then run your surface's quickstart.
Set an [approval policy](concepts/approvals.md) before asking the agent to use tools.
Sessions are [durable by default](concepts/sessions.md); use ephemeral sessions
when the application does not need to resume work.

## Concepts

Semantics live here once. The language directories carry spelling only.

```
concepts/agents.md      building one, and what you may configure
concepts/sessions.md    identity, persistence, resuming, forking
concepts/turns.md       running one, watching one, cancelling one
concepts/events.md      the envelope, the eleven types, the ordering laws
concepts/tools.md       who executes what, and how a call resolves
concepts/skills.md      reusable instructions, named agents, and approved command hooks
concepts/approvals.md   your veto over effects, before they happen
concepts/models.md      one provider, and why the model is a ceiling
concepts/errors.md      the record, the codes, where failures surface
concepts/usage.md       counters, cost, and when snapshots arrive
```

## Configuring and operating

```
configuration.md          knobs settable outside code, and how they resolve
providers.md              provider selection and credentials
versioning.md             what may change under you, and what may not
```

## What you do not have to do

The engine manages the loop, context, delegation, and model selection within your
configured ceiling. Guide its work with instructions, tools, skills, and approvals.

## Maintaining the library

The [implementation architecture](development/architecture.md) describes ownership
and dependency rules for contributors. [Development checks](development/checks.md)
cover source builds and installed-surface verification.
