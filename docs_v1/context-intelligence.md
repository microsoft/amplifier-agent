# Context intelligence

A record of what sessions actually did: every turn, every tool call, every resolution,
every failure, kept somewhere you can ask questions of it later.

There are two ways to get one, and they answer different questions.

## Capture it yourself

The event stream is already a complete account. Every event carries
`(session_id, turn_id, sequence)`, the sequence is contiguous, and nothing is dropped in
transit, so writing each event to your own sink gives you a record you can verify rather
than trust.

```
turn = session.start_turn(input)
for event in turn.events():
    sink.write(event)
```

This is the right answer when the record belongs to your application: your storage, your
schema, your retention. See [events](concepts/events.md).

## Capture wired by the environment

The other kind of record belongs to the environment rather than to the application.
Every session running in that environment is captured the same way, whoever wrote the
code, so a fleet can be looked at as a fleet.

That capture is wired where the agent runs, not in the code that calls it.

```
AMPLIFIER_AGENT_CI_ENDPOINT   where records are sent
AMPLIFIER_AGENT_CI_TOKEN      bearer token for that endpoint
```

Unset the endpoint and nothing is sent anywhere.

These are environment wiring. They are not among the five keys in
[configuration](configuration.md), and there is no `AgentOptions` field that
corresponds to them.

## What arrives

The same eleven event types, in the same envelope, as
[the stream you could have read yourself](concepts/events.md). Records carry the ids
that let separate sessions be related to each other, and are delivered idempotently, so
a retried delivery does not double-count.

Owned extension fields survive intact, which is how a deployment adds its own
correlation without anything having to be taught about it.

## Why there is no knob in code

Whether a fleet is observed is a property of the fleet, not of one caller. An agent that
could opt itself out of the environment's record would make the record worth less than
the effort of collecting it.

An application that wants its own record already has one, from the stream, and owes
nobody a configuration flag to get it.
