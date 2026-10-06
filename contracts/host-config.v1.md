# Host Config Contract v1 (FROZEN 2026-10-02)

**Who builds against this:** host authors, the engine, every binding, every face. The
**host** is the application or environment running the agent, as distinct from the
code that calls the binding.

**What it freezes:** a closed schema for the knobs a host may set outside code.
Small, enumerated, and fail-loud, because configuration drift is a contract violation
rather than a warning.

[`agent-interface.v1`](agent-interface.v1.md) section 2 (`AgentOptions`) is the
programmatic path. This contract governs the **ambient** configuration around it,
meaning environment, files, and defaults, and how the two resolve.

## 1. Resolution order

```text
AgentOptions  >  process env (AMPLIFIER_*)  >  host config file  >  engine defaults
```

Resolved once at agent construction, and immutable for the agent's lifetime.

The registered keys are exactly:

```text
provider              a single value, section 2
model                 the default model, section 2
reasoning_effort      the default reasoning effort, section 2
sessions_directory    a path, section 4
approvals             "allow" | "deny", section 5
extra_request_params  per-provider, settings-only, section 3
context_intelligence  destinations for the observation capture, settings-only, section 4
```

- Booleans parse strictly. `"false"`, `"0"`, and `"no"` are false. Anything else that
  is not a boolean is refused. Ambiguous boolean parsing is a known failure class.
- A key outside that set is refused by name, with the nearest valid key as the remedy.
  The refusal covers host config, not the whole `AMPLIFIER_*` namespace: variables that
  belong to the binding-to-engine seam are not host config and are not read here.
- A key's value is parsed when resolution consults it. A value shadowed by a
  higher-precedence source is not read, and so is not refused.
- A key never silently changes its default within the major version.

## 2. Provider selection

`provider` is a single value. `github-copilot`, the one cross-family aggregator, is
still a single value.
It is available only when its package extra is installed; selecting it without the
extra fails as `engine_unavailable`.

`provider`, `model`, and `reasoning_effort` are the defaults for a new session's
selection, as `agent-interface.v1` section 5 defines. `reasoning_effort` takes the
values and default of that section.

```text
reasoning_effort   env AMPLIFIER_AGENT_REASONING_EFFORT, file { "reasoning_effort": "low" }
```

A consulted value parses strictly. Any other value, including case and whitespace
variants, is refused by name.

Everything else about selection and routing lives in `agent-interface.v1` section 5:
internal, downward-only, and never configurable here. No user-facing routing table
exists.

## 3. Provider state posture

Providers are **stateless by default and by contract**: full input per request, server
retention disabled (for example, OpenAI Responses always sends `store: false` and no
`previous_response_id`), and reasoning continuity carried by bounded local replay.

`extra_request_params`, a per-provider map, is the **settings-only** escape hatch for
deliberate overrides, including an explicit retention opt-in such as
`{ store = true }`.

It never appears on a command line or a face, and nothing in it can change session
semantics. An entry that sets the reasoning effort is refused by name, with
`reasoning_effort` as the remedy. The transcript remains the source of truth.

Request assembly may compact the view sent to the provider once it nears the model's
window: older tool results are truncated, then older messages are dropped, protecting
system content, the first and latest user prompts, and the most recent tool results.
The transcript itself is never compacted. Each tool result entering the transcript is
capped at `tool_result_max_bytes`.

## 4. Sessions directory

`sessions_directory` is the directory an agent keeps its sessions and all other engine
state in. Hosts address that state only through this directory and session ids. A
relative value resolves against the host process's current directory at agent
construction. Agents with the same sessions directory list, resume, and delete the same
sessions, whatever their working directories and whether they run in one process or
several; agents with different ones share nothing.

Absent everywhere, it is computed from the agent's working directory
(`agent-interface.v1` section 2):

```text
~/.amplifier-agent/projects/<slug>/

slug   the absolute working directory with each "/" and "\" replaced by "-" and each
       ":" removed, with a leading "-" added when it does not start with one

/home/me/app          ->  ~/.amplifier-agent/projects/-home-me-app/
C:\projects\web-app   ->  ~/.amplifier-agent/projects/-C-projects-web-app/
```

Durable transcripts persist under it. That is the state the statelessness invariant
(`agent-interface.v1` section 4) relies on.

Durable sessions use the Amplifier session layout:

```text
<sessions_directory>/sessions/<session_id>/
    transcript.jsonl                     the conversation, authoritative
    metadata.json
    context-intelligence/                observation capture
```

The observation capture names the sessions directory's last component as its project.
Hosts and tooling MAY read the layout. Only the engine writes it. Anything else under
the directory, and every migration, is internal.

`context_intelligence` names where the observation capture is forwarded. It is
**settings-only**: no environment form, never on a command line or a face, never in
`AgentOptions`. Absent, the capture stays local. Nothing in it can change session
semantics.

```text
context_intelligence.destinations.<name>  { url, api_key? | auth_mode, auth_resource?, include?, exclude? }
```

The engine reads nothing from any other Amplifier installation's configuration or
environment.

## 5. Approval policy

`approvals` sets the static policy of `agent-interface.v1` section 7 for an agent
constructed without one.

```text
approvals  "allow" | "deny"         env AMPLIFIER_AGENT_APPROVALS, file { "approvals": "allow" }
```

- A consulted value parses strictly. Any other value, including case and whitespace
  variants, is refused by name.
- Any `AgentOptions.approvals`, a handler or a static value, wins. Ambient
  configuration only fills an absent field.
- Absent everywhere, the agent has no approval channel.
- A handler is never ambient configuration, and no face request carries a policy.

## 6. Versioning

`host-config/1`, independent of the other contracts and of releases.

Every change is a dated, owner-ratified amendment in the changelog below.

## Excluded

No promotion path:

- Bundle, module, hook, and orchestrator composition
- Routing configuration
- Modes and recipes. No config key addresses them, ever.
- Per-request configuration on any face
- Approval handlers in ambient configuration

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **Smart-tool registry and discovery config.** The separate registry project ships
  and needs host-side wiring.

## Changelog

Dated, owner-ratified amendments only.

- 2026-10-02: v1 FROZEN by owner ratification.
- 2026-10-05: Breaking, amended in place by owner ratification: `storage` and
  `workspace` replaced by `sessions_directory`, its default computed from the agent's
  working directory (section 4).
- 2026-10-05: Versioning, by owner ratification: the additive-only rule is removed.
  Every change is a dated amendment here; a breaking one is also listed under
  **Breaking** in `CHANGELOG.md`.
- 2026-10-06: Additive, by owner ratification: `reasoning_effort` (sections 1 and 2).
- 2026-10-06: Breaking, by owner ratification: an `extra_request_params` entry that sets
  the reasoning effort is refused (section 3).
- 2026-10-06: Behavior change, by owner ratification: `provider`, `model`, and
  `reasoning_effort` are defaults for a new session rather than ceilings (sections 1
  and 2), following the `agent-interface.v1` amendment of the same date.
