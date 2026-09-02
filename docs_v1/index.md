# Amplifier Agent

A library you embed in your application. Name a model, hand it tools, give it a task. It
reasons, acts, and reports back, emitting a typed event for everything it does along the
way.

The tools decide what it is for. A filesystem and a shell make it a coding agent. Your
deployment API makes it a release agent.

## Three words

```
  your application ---> binding ---,
                                    +---> engine
  your HTTP client ---> face    ---'
```

**Binding.** The library you install and call, one per language. This is the whole of
what you build against.

**Engine.** What runs the agent behind the binding. You never call it, name it, or learn
what it is written in.

**Face.** A network endpoint projecting part of the binding's surface, for callers who
cannot embed a library. A face carries less than a binding does and says so.

Amplifier Agent is a library, not a command line. There is no command to script against.
Anything you want to run from a shell, you write over a binding, in your own repo.

## Pick a surface

```
Python          embed the library                  python/quickstart.md
TypeScript      embed the library                  typescript/quickstart.md
HTTP            point a chat-completions client     http/quickstart.md
```

Bindings are equivalent. Same operations, same events, same failures, spelled the way
each language spells things. The face is deliberately narrower, and
[names what it drops](http/limits.md).

Need approvals, tools your own process executes, or the full event stream? Embed a
binding.

## Start here

```
install.md                install any surface
python/quickstart.md      first agent, first turn, first tool
concepts/                 what everything means, in one place per idea
```

## Concepts

Semantics live here once. The language directories carry spelling only.

```
concepts/agents.md      building one, and what you may configure
concepts/sessions.md    identity, persistence, resuming, forking
concepts/turns.md       running one, watching one, cancelling one
concepts/events.md      the envelope, the eleven types, the ordering laws
concepts/tools.md       who executes what, and how a call resolves
concepts/approvals.md   your veto over effects, before they happen
concepts/models.md      one provider, and why the model is a ceiling
concepts/errors.md      the record, the codes, where failures surface
concepts/usage.md       counters, cost, and when snapshots arrive
```

## Configuring and operating

```
configuration.md          knobs settable outside code, and how they resolve
providers.md              the nine providers and their credentials
context-intelligence.md   capture wired by the environment
versioning.md             what may change under you, and what may not
```

## What you do not have to do

The loop, prompt assembly, context management, sub-agents, and model selection below your
ceiling are ours. You steer with instructions, tools, skills, and approvals.

If using the agent required knowing how the agent is built, something would have leaked.
