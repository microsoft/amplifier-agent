# Configuration

Most of what an agent needs is passed in code, as
[`AgentOptions`](concepts/agents.md). This page covers what a host can set around it:
environment, a file, and the defaults underneath both.

## Resolution

```
AgentOptions  >  AMPLIFIER_AGENT_*  >  config file  >  defaults
```

Resolved once when the agent is built, and fixed for that agent's life.

## The keys

Five, and no more.

```
provider              one provider id
model                 the ceiling
storage               the root durable transcripts are written under
workspace             a slug matching [a-z0-9][a-z0-9-]{0,63}
extra_request_params  per-provider, file only
```

A key outside that set is refused by name, with the nearest valid key offered as the
remedy. A key never quietly changes its default within this major version.

Booleans parse strictly. `false`, `0`, and `no` are false. Anything else that is not a
boolean is refused rather than guessed at.

## Environment

```
AMPLIFIER_AGENT_PROVIDER
AMPLIFIER_AGENT_MODEL
AMPLIFIER_AGENT_STORAGE
AMPLIFIER_AGENT_WORKSPACE
```

`extra_request_params` has no environment form. It is settings-only.

The refusal above covers these four. Other `AMPLIFIER_AGENT_*` variables in your
environment belong to other things and are not read as configuration here.

## File

JSON, at `~/.amplifier-agent/config.json`. Point `AMPLIFIER_AGENT_CONFIG` at a path to
read a different one.

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-5",
  "storage": "/var/lib/amplifier-agent",
  "workspace": "billing-api"
}
```

## extra_request_params

A per-provider map that reaches the provider request verbatim. It is the deliberate
escape hatch, and it exists for the cases nobody can anticipate for you.

```json
{
  "provider": "openai",
  "model": "gpt-5",
  "extra_request_params": {
    "openai": { "store": true }
  }
}
```

Nothing in it can change session semantics. Your transcript stays the source of truth,
whatever a provider is asked to keep. Turning retention on is a deliberate act, taken
here, and never a default. See
[the conversation stays on your side](concepts/sessions.md).

It appears on no face and in no command. If a value can be set from outside your
settings, it is not this.

## What you do not configure

```
composition, bundles, and modules
the loop, and anything observing its lifecycle
prompt assembly
routing tables and model roles
the transcript's on-disk format
```

These are decisions the agent makes so you do not have to. Taking a knob away is only
fair while you still get the result you would have tuned it for, so if one of these is
costing you an outcome rather than just control, that is worth reporting.
