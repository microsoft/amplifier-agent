# Configuration

Most of what an agent needs is passed in code, as
[`AgentOptions`](concepts/agents.md). This page covers what a host can set around it:
environment, a file, and the defaults underneath both.

## Resolution

```
AgentOptions  >  AMPLIFIER_AGENT_*  >  config file  >  defaults
```

Resolved once when the agent is built, and fixed for that agent's life.

Defaults:

```text
provider   anthropic
model      claude-sonnet-5
storage    ~/.amplifier-agent
workspace  default
```

Set both `provider` and `model` when switching providers. Credentials do not select a
provider, and selecting a provider does not choose a matching model automatically.

Tool error recovery is programmatic only: pass `tool_error_policy="continue"` in
Python or `toolErrorPolicy: "continue"` in TypeScript. It defaults to `"stop"` and
has no environment, file, or HTTP request setting. See [tools](concepts/tools.md#recovering-within-a-turn).

## The keys

Six, and no more.

```
provider              one provider id
model                 the ceiling
storage               the root durable sessions are written under
workspace             a slug matching [a-z0-9][a-z0-9-]{0,63}
extra_request_params  per-provider, file only
context_intelligence  observation capture destinations, file only
```

A key outside that set is refused by name, with the nearest valid key offered as the
remedy. A key never quietly changes its default within this major version.

For `store`, `background`, and `parallel_tool_calls`, use JSON `true` or `false`.
The strings `"false"`, `"0"`, and `"no"` also mean false. Numbers and other strings
are refused rather than guessed at.

## Environment

```
AMPLIFIER_AGENT_PROVIDER
AMPLIFIER_AGENT_MODEL
AMPLIFIER_AGENT_STORAGE
AMPLIFIER_AGENT_WORKSPACE
```

`extra_request_params` and `context_intelligence` have no environment form. They are
settings-only.

Misspelled host variables are refused. Variables reserved for the HTTP face and
private runtime connection are handled by their owners.

`workspace` is set through the environment or file; it is not an `AgentOptions`
field. It separates stored sessions and does not restrict filesystem or shell access.

## File

JSON, at `~/.amplifier-agent/config.json`. Point `AMPLIFIER_AGENT_CONFIG` at a path to
read a different one.

The default file may be absent. An explicitly selected file must exist and contain a
JSON object. Relative configuration and storage paths are anchored to the process's
working directory when the agent is constructed, including storage paths read from
the file. Later directory changes do not redirect that agent's transcripts or locks.
Use the same resolved storage root when resuming from another process.

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-5",
  "storage": "/var/lib/amplifier-agent",
  "workspace": "billing-api",
  "context_intelligence": {
    "destinations": {
      "team": { "url": "https://ci.example.test", "api_key": "..." }
    }
  }
}
```

## extra_request_params

A per-provider map of request fields. Accepted fields reach the provider after
validation against the agent's conversation and selection rules.
Responses providers preserve fields absent from the installed SDK's typed methods
in the request body, including during streaming.

```json
{
  "provider": "openai",
  "model": "gpt-5.6-luna",
  "extra_request_params": {
    "openai": { "store": true }
  }
}
```

Nothing in it can change session semantics. Your transcript stays the source of truth,
whatever a provider is asked to keep. Turning retention on is a deliberate act, taken
here, and never a default. See
[the conversation stays on your side](concepts/sessions.md).

Fields that replace input, instructions, model selection, tools, or conversation
identity are refused. Background requests require an explicit `store: true` in the
same settings. Gemini settings must be recognized `GenerateContentConfig` fields;
Copilot's SDK does not accept arbitrary request fields.

It appears on no face and in no command. If a value can be set from outside your
settings, it is not this.

## context_intelligence

Every session keeps an [observation capture](concepts/sessions.md#observation-capture)
beside its transcript. `context_intelligence.destinations` names Context Intelligence
servers the capture is also forwarded to. Absent, the capture stays local.

```json
{
  "context_intelligence": {
    "destinations": {
      "team": {
        "url": "https://ci.example.test",
        "api_key": "...",
        "include": ["**"],
        "exclude": ["**/scratch/**"]
      },
      "audit": {
        "url": "https://audit.example.test",
        "auth_mode": "entra",
        "auth_resource": "api://audit"
      }
    }
  }
}
```

Each destination needs a `url` and one credential form: `api_key`, or `auth_mode:
"entra"` with `auth_resource`. `include` and `exclude` are gitignore-style patterns
matched against the agent's working directory; `include` defaults to everything and
`exclude` wins. Unknown fields are refused by name.

Forwarding is best effort. A refused or unreachable destination is recorded under
`<storage>/context-intelligence-logs/` and never fails a turn. Nothing here can change
session semantics, and the engine reads no other Amplifier installation's settings,
keys, or `AMPLIFIER_*` variables to find a destination.

## What you do not configure

```
composition, bundles, and modules
the loop, and anything observing its lifecycle
prompt assembly
routing tables and model roles
```

These are decisions the agent makes so you do not have to. Taking a knob away is only
fair while you still get the result you would have tuned it for, so if one of these is
costing you an outcome rather than just control, that is worth reporting.
