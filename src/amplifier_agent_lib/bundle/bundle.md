---
bundle:
  name: amplifier-agent-behavioral-anchor
  version: 0.1.0
  description: |
    Vendored opinionated manifest for the amplifier-agent CLI. Adapted from
    the experimental behavioral-anchor bundle
    (amplifier-foundation@main:experiments/behavioral-anchor/behavioral-anchor.md).

    Behavior is shaped by a small set of named principles loaded once at the
    head of the system prompt, backed by thin purposeful agents and a standard
    tool roster inherited by sub-agents through tool-delegate.

    AAA-specific modifications from upstream behavioral-anchor:
      - default_provider: anthropic         (engine reads this directly)
      - hook-context-intelligence           (preserves workspace JSONL alignment
                                             with amplifier-app-cli, per
                                             docs/designs/2026-06-09-workspace-resolution-and-migration.md
                                             invariant I8)
      - tool-mcp                            (preserves MCP support for existing users)
      - DROPPED hooks-streaming-ui          (would break JSON-stdout contract;
                                             engine handles streaming via
                                             bundle/hook_streaming.py mounted
                                             programmatically by _runtime.py
                                             and spawn.py)
      - DROPPED hooks-todo-display          (would break JSON-stdout contract)
      - DROPPED behaviors/logging.yaml      (replaced by hook-context-intelligence)
      - DROPPED hooks-approval              (wire protocol has no approval
                                             round-trip yet; would deadlock on
                                             policy-driven rules)

    Per the Strategy 1 decision (docs/designs/2026-05-19-baked-in-bundle-decision.md),
    no `includes:` block. Everything declared inline. Manifest text + agent
    definitions + context/system.md are vendored inside the wheel; every other
    module is git-cloned and pip-installed on first invocation. The prepared
    result is cached to
    ~/.amplifier-agent/cache/prepared/<aaa_version>/<sha256(bundle.md)>/
    (override the root via $AMPLIFIER_AGENT_HOME).

    Editing this file changes the cache key (sha256) and self-invalidates
    the warm pickle.

# Engine-level default provider routing. Read by the host/CLI config layer
# to seed the default provider selection before any host-supplied override
# applies. Sibling to bundle:/session:/tools:/hooks:/agents: (top-level key,
# not nested under bundle:).
default_provider: anthropic

# Install-only stubs for the 4 default providers.  Declaring all 4 here causes
# bundle.prepare(install_deps=True) to clone and editable-install each provider
# module into the tool venv during the cold-prepare step (and post-install hook).
# This guarantees every provider module is importable before any AmplifierSession
# is created -- which matters for `amplifier-agent serve chat-completions`, where
# the lifespan calls list_provider_models() BEFORE creating the first session.
#
# These entries carry NO config / credentials.  Credentials flow from env vars
# at runtime via inject_provider (single_turn.py) and _session_runner.py.
# Both callers clear mount_plan["providers"] = [] before calling inject_provider
# so the kernel only mounts the single user-selected provider, not all 4 stubs.
providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
  - module: provider-openai
    source: git+https://github.com/microsoft/amplifier-module-provider-openai@main
  - module: provider-azure-openai
    source: git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main
  - module: provider-ollama
    source: git+https://github.com/microsoft/amplifier-module-provider-ollama@main

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
    # NOTE: intentional divergence from upstream behavioral-anchor.md (which
    # has no provider block).  This entry is the RUNTIME default: when
    # host_config does not specify a provider, _read_bundle_default_provider()
    # returns "anthropic" from the top-level `default_provider:` field and
    # inject_provider mounts this module at session creation.
    # The actual install of provider-anthropic (and the other 3 providers)
    # is handled by the top-level `providers:` stubs above; foundation's
    # bundle.prepare() processes that list, NOT this session.provider block.
    module: anthropic-provider
    source: git+https://github.com/microsoft/amplifier-module-anthropic-provider@main

# Tools declared at the parent (orchestrator) level. Sub-agents inherit them
# via tool-delegate's `context_inheritance.enabled: true` -- agents do NOT
# declare their own tools blocks. See agents/*.md for evidence.
tools:
  # Core tools (inherited by all sub-agents via tool-delegate)
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-apply-patch
    source: git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=modules/tool-apply-patch

  # Agent delegation
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      features:
        self_delegation:
          enabled: true
        session_resume:
          enabled: true
        context_inheritance:
          enabled: true
          max_turns: 10
        provider_selection:
          enabled: true
      settings:
        # Sub-agents do NOT inherit tool-delegate from the parent. They get
        # the inherited tool roster (filesystem/bash/web/search/todo/apply-patch)
        # and can self-delegate for parallel-dispatch patterns at the
        # specialist layer if they re-acquire tool-delegate themselves.
        exclude_tools: [tool-delegate]

  # MCP (AAA-specific addition vs upstream behavioral-anchor)
  - module: tool-mcp
    source: git+https://github.com/microsoft/amplifier-module-tool-mcp@main
    config:
      verbose_servers: false
      max_content_size: 65536

  # Skills (discovery available, auto-injection disabled to save tokens)
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
    config:
      skills:
        - git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills
      visibility:
        enabled: false

  # Mode switching
  - module: tool-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/tool-mode
    config:
      gate_policy: warn

  # Recipes
  - module: tool-recipes
    source: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=modules/tool-recipes
    config:
      session_dir: ~/.amplifier/projects/{project}/recipe-sessions
      auto_cleanup_days: 7

# Hooks declared inline. AAA-specific modifications from upstream behavioral-anchor:
#   - DROPPED hooks-streaming-ui  (stdout contract violation; engine uses bundle/hook_streaming.py)
#   - DROPPED hooks-todo-display  (stdout contract violation)
#   - DROPPED behaviors/logging.yaml include  (replaced by hook-context-intelligence below)
#   - DROPPED hooks-approval      (no wire-protocol approval round-trip yet)
#   - ADDED   hook-context-intelligence  (workspace JSONL alignment with amplifier-app-cli)
hooks:
  # === Free-cost UX hooks ===
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

  # === Mode enforcement ===
  - module: hooks-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/hooks-mode
    config:
      search_paths: []

  # === Model routing ===
  # Resolves each agent's model_role frontmatter against a curated provider/model
  # matrix at session:start and overwrites provider_preferences accordingly.
  # Inlined per the Strategy 1 decision (no includes:) — equivalent to including
  # @routing-matrix:behaviors/routing.yaml. The balanced matrix tries Anthropic
  # candidates first, with cross-provider fallback through the candidate list;
  # provider availability filtering is automatic.
  - module: hooks-routing
    source: git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=modules/hooks-routing
    config:
      default_matrix: balanced

  # === Observability hooks (local-only event logging) ===
  # Captures kernel + delegate lifecycle events to JSONL alongside transcripts
  # and audits in the workspace tree (invariant I8 -- unified per-session
  # layout). No remote dispatch -- server URL and API key are intentionally
  # not set, so the hook operates in local-logging mode.
  - module: hook-context-intelligence
    # TODO(upstream-tag): tighten from @main to @v0.1.2 once the maintainer cuts a tag.
    # See prior bundle.md history for full context. PR #36 merged the standalone-install
    # fix; awaiting a tag.
    source: git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/hook-context-intelligence
    config:
      log_level: INFO
      # Hook computes: <base_path>/<project_slug>/sessions/<id>/context-intelligence/
      # project_slug is seeded from coordinator.config["project_slug"] (D5).
      base_path: "~/.amplifier-agent/state/workspaces"
      additional_events:
        - delegate:agent_spawned
        - delegate:agent_resumed
        - delegate:agent_completed
        - delegate:agent_cancelled
        - delegate:error

# The sub-session agents this bundle ships.
# Definitions are vendored at src/amplifier_agent_lib/bundle/agents/<name>.md.
# Agents have NO tools blocks -- they inherit the parent's tool roster
# (filesystem, bash, web, search, todo, apply-patch) via tool-delegate's
# context_inheritance.
agents:
  include:
    - explorer
    - architect
    - builder
    - debugger
    - git-ops
    - researcher
---

# Behavioral Anchor (amplifier-agent built-in)

A lean, principle-driven bundle. Behavior is shaped by a short set of named
principles loaded once at the head of the system prompt, backed by thin
purposeful agents and a standard tool roster inherited by sub-agents.

@amplifier-agent-behavioral-anchor:context/system.md
