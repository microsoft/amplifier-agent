# Amplifier Agent, Living Vision

Where Amplifier Agent is going, written in the present tense, as if it had arrived.
Nothing here records what is built. That is the issue queue and the git history. When
the destination changes, sentences are rewritten in place so the page reads as one
plan.

## What Amplifier Agent is

A library you embed in your application. You name a model, hand it tools, and give it
a task. It reasons, acts, and reports back, emitting a typed event for everything it
does along the way.

The tools decide what it is for. A filesystem and a shell make it a coding agent. Your
deployment API makes it a release agent.

Callers reach it through one small library per supported language, idiomatic in
spelling and identical in behavior. Which languages get one is a delivery decision,
made in the issue queue, not a promise made here.

Behind that library is the engine: the part that actually runs the agent. It is ours.
Assembly, routing, context management, and delegation happen there, on your behalf,
and none of it is work you inherit.

### A library, not a command line

There is no command for you to script against, and there will not be one.

A command line good enough to depend on becomes the surface everyone integrates
against, and argv cannot evolve the way a typed interface can. Shipping one would
quietly make it the product and freeze it by usage rather than by decision. Any CLI in
this repo exists for development and verification. A caller who wants one writes it
over a library, in their own repo.

## Principles

1. **The library's surface is the product.** It holds still while the code behind it
   gets better, so you integrate once and collect every improvement after that. That
   only works if the surface stays small, deliberately smaller than what the engine
   can do.

2. **You call a library, never the engine.** Today the engine is built on
   AmplifierSession, amplifier-core, and amplifier-foundation. You cannot tell, and
   that is the point. The language the engine happens to be written in gets a library
   like every other language, with no shortcut, because one shortcut is what would
   make today's engine permanent.

3. **We do the agent engineering, you do the steering.** The loop, prompt assembly,
   context management, sub-agents, and routing are ours. You steer with instructions,
   tools, skills, and approvals. If using the agent requires knowing how the agent is
   built, something has leaked, and a leak is a defect.

4. **If we take the knob away, we owe you the result.** Most of what makes an agent
   good is hidden, and each hidden thing is something you cannot tune. That is only a
   fair trade while you still get the outcome you would have tuned it for. Hiding the
   routing table is fine as long as sensible model selection still happens. An
   exclusion that costs you the result, and not just the control, is a defect.

5. **The model you name is a ceiling.** It is the most expensive thing we will run on
   your behalf. We may drop below it when something cheaper will do, or when the model
   you named is overloaded, and we stay inside the provider you chose. We never go
   above it. Nobody gets a surprise Opus bill from an agent they configured for Haiku.

6. **The event stream is how you watch the work.** Reasoning, replies, tool calls,
   results, and usage all arrive as typed events. The vocabulary is closed and
   identical in every language, so a renderer written once works everywhere. A library
   that invents its own event type has forked the product.

7. **You keep authority over every effect.** The model decides when a tool should run.
   Some tools are yours, and we call into your application to run them. Some come with
   the agent, and we run those ourselves. Either way you see the request before
   anything happens, you can refuse it, and you get one truthful answer about the
   outcome. We never run your code outside your process, never retry an effect that
   may already have landed, and never claim to have undone one. If a tool cannot say
   whether it worked, we pass that uncertainty through rather than rounding it to
   success or failure.

8. **Your conversation stays on your side.** History lives where you run the agent,
   not on the model provider's servers. Every request carries the full conversation
   and asks the provider to keep nothing. Three things follow: work survives any
   process dying between turns, a session resumes days later, and no provider ends up
   holding a copy of your conversation.

9. **Failures are loud and named.** Every failure carries a code, a message, and a
   remedy you can act on. Nothing degrades quietly. A run that reports success while
   accomplishing nothing is the one failure that costs you your trust in every run
   before it.

10. **The repository is the record.** Breaking changes live in commits and pull
    requests, not in a changelog maintained by hand. A contract's changelog records
    the one thing a commit cannot: why a ratified decision changed.

## Shape

1. **Bindings.** The per-language libraries callers hold, governed by
   [`language-binding.v1.md`](language-binding.v1.md) and presenting
   [`agent-interface.v1.md`](agent-interface.v1.md) whole.
2. **Faces.** Projections onto network protocols for callers who cannot embed a
   library, such as [`http-face.v1.md`](http-face.v1.md). A face declares what it
   drops and never becomes the definition of the product.
3. **The binding-to-engine seam.** Internal and owned. What it must be able to do is
   pinned in [`engine-seam.v1.md`](engine-seam.v1.md); how it does it is pinned nowhere,
   so each binding couples and re-couples freely.
4. **The engine.** One occupant of its slot, not the definition of it. Its internals,
   including any mode-like or recipe-like mechanisms, stay hidden.

## What this deliberately resists

- **A caller-facing command line.** Covered above, and worth resisting every time it
  is proposed.
- **Compatibility before stability.** The surface is young. It breaks cleanly and
  callers are fixed. Stabilization is the freeze bar, taken deliberately.
- **Opening an internal to buy a feature.** Solve the need behind the library instead.
- **Knobs for decisions we should be making.** A setting is admitted only when we
  cannot know and the caller can.
- **Python shapes, transports, or adapter formats becoming the contract.** Chat
  completions is a face. A subprocess is plumbing.
- **Vocabulary from one caller's domain.** Coding, release, and support agents are
  served equally.

## This page and the contracts

The contracts pin surfaces. This page sets direction. Neither one overrules the other
in its own territory: a contract never dictates where we are going, and this page
never dictates a signature.

Its one demand on the contracts is that none of them may pin something that puts this
direction out of reach. That is why the libraries are contracted now, rather than on
the day the engine needs replacing.

## Changelog

Dated, owner-ratified amendments only.
