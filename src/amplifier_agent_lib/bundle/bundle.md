---
bundle:
  name: amplifier-agent-builtin
  version: 1.1.0
  description: >
    Vendored opinionated manifest for the amplifier-agent CLI. Aligned with the
    upstream build-up-foundation experimental bundle
    (amplifier-foundation@main:experiments/build-up/behaviors/build-up-foundation.yaml,
    v0.4.0). Per the Strategy 1 decision
    (docs/designs/2026-05-19-baked-in-bundle-decision.md), the 4 behavior
    `includes:` upstream uses are inlined directly here — the bundle is
    self-describing without depending on foundation's named-bundle registry.
    The manifest text and the four sub-session agent definitions are vendored
    inside the wheel; every other module is git-cloned and pip-installed on
    first invocation. The prepared result is cached to
    $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)>/.
    Editing this file changes the cache key (sha256) and self-invalidates
    the warm pickle.

session:
  raw: true
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true

  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 300000
      compact_threshold: 0.8
      auto_compact: true

  provider:
    # NOTE: kept (intentional divergence from upstream build-up-foundation.yaml,
    # which has no provider block). Declaring it here ensures the cold-prepare
    # step installs the provider module. The actual env-var → mount_plan
    # provider entry is injected at runtime by _runtime.make_turn_handler
    # (Option C — provider_sources.py). Upstream relies on the app layer to
    # both install AND inject; we do install-via-bundle + inject-via-app.
    module: anthropic-provider
    source: git+https://github.com/microsoft/amplifier-module-anthropic-provider@main

# Parent-level tools — ONLY what the orchestrator itself uses.
# Sub-agent tools (bash, filesystem, search, edit_file, etc.) live in each
# agent's own .md frontmatter under `tools:`, and get activated at compose
# time via the spawn-side wiring (src/amplifier_agent_lib/spawn.py).
tools:
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      features:
        # Disabled at the parent so the orchestrator cannot delegate to itself.
        # The build-up parent has only `todo` + `delegate` — spawning another
        # instance of the parent (which is what agent="self" does) produces a
        # sub-instance with no real tools (no bash/filesystem/search/edit_file)
        # that can only thrash mode/todo until context overflows. Sub-agents
        # (explorer, planner, coder, tester) keep self-delegation enabled in
        # their own frontmatter for legitimate parallel-dispatch patterns.
        self_delegation:
          enabled: false
        session_resume:
          enabled: true
        context_inheritance:
          enabled: true
          max_turns: 10
        provider_selection:
          enabled: true
      settings:
        # Sub-agents do NOT inherit the parent's tool-delegate instance. They
        # declare their own tool-delegate in their .md frontmatter (independent
        # configuration — sub-agents allow self-delegation for parallel-dispatch
        # patterns at the specialist layer).
        exclude_tools: [tool-delegate]

# Hooks declared inline (per Strategy 1 D6 — no `includes:` block, no registry
# dependencies). Mirrors upstream build-up-foundation's hooks block AND the
# 4 behavior YAML includes (status-context, redaction, logging) — but
# OMITS the two hooks that write to stdout, which would break this CLI's
# JSON-output contract:
#
#   - hooks-streaming-ui (Thinking/ToolCall/TokenUsage blocks to stdout)
#   - hooks-todo-display (progress bar / border to stdout)
#
# Upstream is designed for a TUI host that consumes those bytes; our CLI
# emits JSON on stdout and diagnostics on stderr, so TUI hooks are an
# architectural mismatch. The remaining 5 hooks inject into the LLM's
# context or write to logging files — they don't touch stdout.
hooks:
  # === Free-cost UX hooks (from upstream behaviors/) ===
  - module: hooks-status-context
    source: git+https://github.com/microsoft/amplifier-module-hooks-status-context@main
    config:
      include_datetime: true
      datetime_include_timezone: false
  - module: hooks-redaction
    source: git+https://github.com/microsoft/amplifier-module-hooks-redaction@main
    config:
      allowlist:
        - session_id
        - turn_id
        - span_id
        - parent_span_id
  - module: hooks-logging
    source: git+https://github.com/microsoft/amplifier-module-hooks-logging@main
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl

  # === Productivity hooks ===
  - module: hooks-todo-reminder
    source: git+https://github.com/microsoft/amplifier-module-hooks-todo-reminder@main
    config:
      inject_role: user
      priority: 10
  - module: hooks-session-naming
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming
    config:
      initial_trigger_turn: 2
      update_interval_turns: 5

# The four self-sufficient sub-session agents this bundle ships.
# Definitions are vendored at src/amplifier_agent_lib/bundle/agents/<name>.md;
# the loader hydrates them at compose time into overlay-shaped dicts that the
# spawner (src/amplifier_agent_lib/spawn.py) deep-merges with the parent
# config when the `delegate` tool requests them.
agents:
  include:
    - explorer
    - planner
    - coder
    - tester
---

# amplifier-agent Built-in Bundle (Vendored Opinionated Manifest)

This bundle defines the runtime environment for the **amplifier-agent CLI**.
It mirrors the upstream **build-up-foundation** experimental bundle
(`amplifier-foundation@main:experiments/build-up/behaviors/build-up-foundation.yaml`,
v0.4.0), with the behavior `includes:` inlined directly per the Strategy 1
decision in `docs/designs/2026-05-19-baked-in-bundle-decision.md`. Module
sources use `@main`; upstream module updates flow automatically.

## Tool surface at the parent (orchestrator) level

The parent agent loads exactly two tools: `todo` (planning) and `delegate`
(sub-session dispatch). All concrete work — reading files, running commands,
searching, editing, running tests — goes through one of the four sub-session
agents below, each of which carries its own tool surface in its frontmatter.

| Agent | Tools (declared in frontmatter) |
|---|---|
| **parent** | `todo`, `delegate` (orchestration only) |
| `explorer` | `bash`, `filesystem`, `search`, `todo`, `delegate` (read-only) |
| `planner`  | `filesystem`, `todo`, `delegate` (design / spec / review) |
| `coder`    | `bash`, `filesystem`, `search`, `todo`, `delegate` (implementation from spec) |
| `tester`   | `bash`, `filesystem`, `search`, `todo`, `delegate` (verification, test gen) |

Agent definitions are vendored in `agents/{explorer,planner,coder,tester}.md`
adjacent to this file. Editing them changes the manifest content hash and
self-invalidates the warm cache.

## Delegation mechanics

The `delegate` tool spawns sub-sessions. Targets:

| Form | Meaning |
|---|---|
| `agent="<name>"` (e.g. `"explorer"`) | One of the four bundled specialists |
| `agent="self"` | **Disabled at the parent level** — see `self_delegation.enabled: false` in the manifest. Sub-agents may still self-delegate. |

Context control:

| Parameter | Values |
|---|---|
| `context_depth` | `none`, `recent`, `all` |
| `context_scope` | `conversation`, `agents`, `full` |

- Independent subtask: `context_depth="none"`.
- Sub-session B sees sub-session A's output: `context_scope="agents"`.
- Self-delegation continuing heavy work (at sub-agent layer only): `context_depth="all", context_scope="full"`.

### Parallel dispatch

```python
delegate(agent="explorer", instruction="Check frontend", context_depth="none")
delegate(agent="explorer", instruction="Check backend", context_depth="none")
```

### Session resume

```python
r = delegate(agent="explorer", instruction="Start analysis")
delegate(session_id=r["session_id"], instruction="Now examine edge cases")
```

### Relay results

The user sees only your final response text. When a sub-session returns
findings, summarize them in your final response. Do not assume the user
saw raw tool output.

## Runtime context

The agent runs inside the `amplifier-agent` CLI process. Approval flows and
display updates are mediated by the host adapter — the component that bridges
agent-side events (tool calls, approval requests, stream chunks) to the host
application (e.g. the Paperclip VS Code extension or any compliant JSON-RPC
client).

Session-transcript persistence (writing to
`$XDG_STATE_HOME/amplifier-agent/sessions/<session-id>/`) is **not** owned
by the context module declared above (`context-simple`); it remains a future
CLI-layer hook concern. Out of scope for this manifest.

## Bundle stability

This manifest text is **sealed per release**. Module `source:` URLs use
`@main`, so upstream module updates flow automatically — drift is intentional
product behaviour, not a defect. Editing this file changes the cache key
(sha256) and self-invalidates all cached prepared bundles. Any change must
be intentional and reviewed as a design decision.
