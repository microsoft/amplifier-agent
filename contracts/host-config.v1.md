# Host Config Contract v1 (FROZEN 2026-09-02)

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
model                 the ceiling, section 2
storage               the storage root, section 4
workspace             a slug, section 4
extra_request_params  per-provider, settings-only, section 3
context_intelligence  destinations for the observation capture, settings-only, section 4
```

- Booleans parse strictly. `"false"`, `"0"`, and `"no"` are false. Anything else that
  is not a boolean is refused. Ambiguous boolean parsing is a known failure class, and
  the kit pins it.
- A key outside that set is refused by name, with the nearest valid key as the remedy.
  The refusal covers host config, not the whole `AMPLIFIER_*` namespace: variables that
  belong to the binding-to-engine seam are not host config and are not read here.
- A key never silently changes its default within the major version.

## 2. Provider selection

`provider` is a single value. `github-copilot`, the one cross-family aggregator, is
still a single value.

`model` names the ceiling.

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
semantics. The transcript remains the source of truth.

Request assembly may compact the view sent to the provider once it nears the model's
window: older tool results are truncated, then older messages are dropped, protecting
system content, the first and latest user prompts, and the most recent tool results.
The transcript itself is never compacted. Each tool result entering the transcript is
capped at `tool_result_max_bytes`.

## 4. Storage and workspace

The engine owns its storage home. Hosts address it only through the `storage` root,
`workspace` slugs matching `[a-z0-9][a-z0-9-]{0,63}`, and session ids.

Durable transcripts persist under that root. That is the state the statelessness
invariant (`agent-interface.v1` section 4) relies on.

Durable sessions use the Amplifier session layout:

```text
<storage>/workspaces/<workspace>/sessions/<session_id>/
    transcript.jsonl                     the conversation, authoritative
    metadata.json
    context-intelligence/                observation capture
```

Hosts and tooling MAY read it. Only the engine writes it. Anything else under the
root, and every migration, is internal.

`context_intelligence` names where the observation capture is forwarded. It is
**settings-only**: no environment form, never on a command line or a face, never in
`AgentOptions`. Absent, the capture stays local. Nothing in it can change session
semantics.

```text
context_intelligence.destinations.<name>  { url, api_key? | auth_mode, auth_resource?, include?, exclude? }
```

The engine reads nothing from any other Amplifier installation's configuration or
environment.

## 5. Versioning

`host-config/1`, independent of the other contracts and of releases.

Additive only: new optional keys may appear, and none is removed, renamed, re-typed,
or re-defaulted.

## Excluded

No promotion path:

- Bundle, module, hook, and orchestrator composition
- Routing configuration
- Modes and recipes. No config key addresses them, ever.
- Per-request configuration on any face

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **Smart-tool registry and discovery config.** The separate registry project ships
  and needs host-side wiring.

## Conformance

- Resolution-order fixtures, per layer and per override direction
- Strict boolean parsing, with garbage refused
- Unregistered-key refusal, carrying the nearest-key remedy
- Single-provider enforcement, where a list is refused
- `extra_request_params` reaching the wire verbatim while absent from every CLI and
  face surface
- A record-and-replay fixture proving no request carries provider conversation state
  unless explicitly opted in
- `context_intelligence` absent from every environment and face surface; a
  destination fixture receives the capture

## Changelog

Dated, owner-ratified amendments only.

- 2026-09-02: v1 FROZEN by owner ratification. Freeze bar at stamp time: the
  spec exists.
- 2026-09-22: Owner-ratified amendment: durable sessions use the Amplifier session
  layout under the storage root, and the settings-only `context_intelligence` key
  names destinations for the observation capture.

