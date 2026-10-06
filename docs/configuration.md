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
provider            anthropic
model               claude-sonnet-5
reasoning_effort    medium
sessions_directory  ~/.amplifier-agent/projects/<computed from the working directory>/
```

Set both `provider` and `model` when switching providers. Credentials do not select a
provider, and selecting a provider does not choose a matching model automatically.

`tool_error_policy` (`toolErrorPolicy`) and `tool_result_max_bytes`
(`toolResultMaxBytes`) are programmatic only, with no environment, file, or HTTP
request setting. See [tools](concepts/tools.md).

## The keys

Seven, and no more.

```
provider              one provider id
model                 the ceiling
reasoning_effort      the reasoning ceiling
sessions_directory    where sessions and engine state are kept
approvals             the static approval policy, "allow" or "deny"
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
AMPLIFIER_AGENT_REASONING_EFFORT
AMPLIFIER_AGENT_SESSIONS_DIRECTORY
AMPLIFIER_AGENT_APPROVALS
```

`extra_request_params` and `context_intelligence` have no environment form. They are
settings-only.

Misspelled host variables are refused. Variables reserved for the HTTP face and
private runtime connection are handled by their owners.

`approvals` takes exactly `"allow"` or `"deny"`. It applies only when `AgentOptions`
sets no `approvals`, handler or policy, and only then is any other value refused. A
handler is never set here. See [approvals](concepts/approvals.md).

`reasoning_effort` takes exactly one of `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, or `max`, from `AgentOptions`, then the environment, then the file. Case and
whitespace variants are refused by name. See
[reasoning effort](concepts/models.md#reasoning-effort).

## Sessions directory

`sessions_directory` holds an agent's durable sessions and the rest of its state.
Agents with the same sessions directory list, resume, and delete the same sessions;
agents with different ones share nothing. It does not restrict filesystem or shell
access. A leading `~` is expanded.

Absent everywhere, it is computed from the agent's
[working directory](concepts/agents.md#working-directory), so each folder gets its own:

```text
~/.amplifier-agent/projects/<slug>/

slug   the full path with each "/" and "\" replaced by "-" and each ":" removed,
       with a leading "-" added when it does not start with one

/home/me/app          ->  ~/.amplifier-agent/projects/-home-me-app/
C:\projects\web-app   ->  ~/.amplifier-agent/projects/-C-projects-web-app/
```

This is the project slug Amplifier CLI uses, so Context Intelligence groups CLI and
agent sessions for the same folder as one project.

Set it in `AgentOptions`, the environment, or the file to share sessions across
folders, or to keep them with a project, such as `<project>/.amplifier-agent`. Add
that folder to the project's `.gitignore`. The observation capture names the
directory's last component as its project.

To keep one history for work spread over several folders, give each agent its own
working directory and the same sessions directory:

```text
working_directory          sessions_directory
/home/me/notes/finance     /home/me/.amplifier-agent/projects/personal
/home/me/notes/health      /home/me/.amplifier-agent/projects/personal
```

Each agent reads its own folder's instructions and works on its own files. Sessions
from both are listed together, captured as one project, `personal`, and can be resumed
from either folder. A resumed session works in the resuming agent's working directory.

## File

JSON, at `~/.amplifier-agent/config.json`. Point `AMPLIFIER_AGENT_CONFIG` at a path to
read a different one.

The default file may be absent. An explicitly selected file must exist and contain a
JSON object. A relative config file path or `sessions_directory` is anchored to the
process's current directory when the agent is constructed, including a
`sessions_directory` read from the file. Later directory changes do not redirect that
agent's transcripts or locks. Use the same resolved sessions directory when resuming
from another process.

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-5",
  "reasoning_effort": "low",
  "sessions_directory": "/var/lib/amplifier-agent/billing-api",
  "approvals": "deny",
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

```json
{
  "provider": "openai",
  "model": "gpt-6-luna",
  "extra_request_params": {
    "openai": { "store": true }
  }
}
```

Nothing in it can change session semantics. Your transcript stays the source of truth,
whatever a provider is asked to keep. Turning retention on is a deliberate act, taken
here, and never a default. See
[the conversation stays on your side](concepts/sessions.md).

Keys must be registered provider ids. Fields that replace input, instructions, model
selection, tools, conversation identity, or connection settings are refused.
Background requests require an explicit `store: true` in the same settings, and
`truncation` is accepted only as `"disabled"`. `openai-chatgpt` refuses `store: true`,
`max_output_tokens`, `temperature`, `truncation`, `parallel_tool_calls`, and `include`.
Gemini settings must be recognized `GenerateContentConfig` fields; Copilot's SDK
does not accept arbitrary request fields.

Entries that set the reasoning effort are refused by name; set `reasoning_effort`
instead. Keys match ignoring case and underscores:

```text
every provider                          reasoning_effort
openai, azure-openai, openai-chatgpt    reasoning
vllm                                    reasoning
anthropic                               thinking, output_config carrying effort
gemini                                  thinking_config
ollama                                  think
```

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
matched against the agent's [working directory](concepts/agents.md#working-directory);
`include` defaults to everything and `exclude` wins. Unknown fields are refused by name.

Forwarding is best effort. A refused or unreachable destination is recorded in the
[sessions directory](#sessions-directory) and never fails a turn. Nothing here can
change session semantics, and the engine reads no other Amplifier installation's
settings, keys, or `AMPLIFIER_*` variables to find a destination.

## What you do not configure

```
composition, bundles, and modules
the loop, and anything observing its lifecycle
prompt assembly
routing tables and model roles
```

The agent makes these decisions. If one costs you an outcome, report it.
