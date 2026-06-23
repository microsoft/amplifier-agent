# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Chat-completions session resume via `X-Client-Session-Id`.** When the
  client sends this header, amplifier-agent now uses a deterministic
  ``http-<client_sid>`` as the amplifier session_id, auto-detects whether
  this is the first turn or a continuation by checking if the session state
  dir exists on disk, and passes ``is_resumed`` to the existing kernel
  resume mechanism (same primitive the CLI face's ``--resume`` flag uses).
  One opencode conversation = one amplifier session — unified audit trail,
  persistent hook state across turns, append-mode events.jsonl.

- **Client-authoritative transcript reconciliation** in
  ``src/amplifier_agent_http/_reconciler.py``.  Since the chat-completions
  wire is stateless and the client sends full history every turn, on
  divergence between stored and incoming the client wins by fiat — we
  persist the client's view over our stored copy without any rewind
  ceremony.  Sufficient for opencode and any well-behaved OpenAI-compatible
  client.  No new event types introduced.

- **`bundle.md` declares all 4 default providers** (anthropic, openai, azure-openai, ollama). Previously only anthropic was declared; the other 3 had to be installed lazily at first use of `amplifier-agent run` against a host_config that referenced them. Now all 4 ship as part of the prepared bundle — the top-level `providers:` section is processed by `bundle.prepare(install_deps=True)` during cold-prep and the post-install hook, ensuring every provider module is importable before any session is created.

### Changed

- **`amplifier-agent serve chat-completions` lifespan now triggers the same module-install path that `amplifier-agent run` uses.** Previously, a fresh `uv tool install amplifier-agent` followed by `serve chat-completions` would fail with `ProviderModuleNotInstalledError` because the lazy-install that `run` gets via `create_session() → session.initialize() → resolver.async_resolve()` never fires for `serve`. The lifespan now calls `prepared.resolver.async_resolve(module_id, source)` for every `PROVIDER_CATALOG` entry before the providers loop — idempotent (no-op on warm cache) and asynchronous (lifespan waits for completion before opening the wire).

- **`single_turn.py` now explicitly clears `mount_plan["providers"]` before `inject_provider`.** `bundle.md` now populates the top-level `providers:` section with 4 stubs so `bundle.prepare()` installs them. Without the clear, `inject_provider`'s "no-op if providers already present" guard would fire and skip injecting the runtime provider (with env-var credentials). This mirrors the pattern already used by `_session_runner.run_chat_turn`.

- **Breaking (server mode only):** `amplifier-agent serve chat-completions` now requires `host_config.providers` to be a non-empty dict. Any provider declared there that cannot initialize (missing credentials, module not installed, `list_models()` raises, returns 0 models) causes the server to exit 2 with a structured error listing every problem. The previous behavior — iterating a hardcoded `KNOWN_PROVIDERS` list, silently skipping unreachable providers, and falling back to an unusable placeholder model — is gone. Single-turn mode (`amplifier-agent run`) is unaffected; the `provider` (singular) block continues to work for it.

- **`POST /v1/chat/completions` now validates `model` against the served registry.** Requests with an unknown model return HTTP 400 `{"error": {"code": "unknown_model", ...}}` immediately, instead of being silently routed to whichever provider loaded first and failing 4 seconds later with an upstream `not_found_error` embedded in `delta.content`.

- **`stream: false` is now honored.** Requests with that flag return a single JSON body; only `stream: true` (or absent) uses SSE.

- **Upstream errors raised before any content chunks are emitted now surface as HTTP 502** with a structured OpenAI-shape error envelope, instead of being embedded inside `delta.content` of a 200 SSE response.

- **`/v1/models` no longer falls back to a placeholder `{"id": "amplifier", ...}` entry.** The lifespan now guarantees `served_models_registry` is non-empty (or the server exits at boot), so the fallback was unreachable in practice.

### Added

- **`amplifier-agent serve status / stop / restart` subcommands** — operational lifecycle for the chat-completions HTTP server. Status reports whether the server is running, where it's reachable, how many models from which providers it's serving, and self-cleans stale state files when the PID no longer exists. Stop sends SIGTERM with a configurable graceful-exit window (`--timeout`), escalating to SIGKILL on expiry or on `--force`. Restart performs an identity-restart using the args stored at original launch (host, port, api-key, workspace, host_config). State is tracked in `~/.amplifier-agent/state/serve.json` (mode 0600, parent dir 0700; api_key is sensitive — never logged).

- **`host_config.providers` (plural) registry** — declares which providers the server-mode lifespan loads and how to instantiate each. Schema: `providers: {<provider_id>: {module?: str, config?: dict}}`. The `module` defaults to the provider_id when omitted. Each provider's `config` is passed through as the `extra_config` arg to `list_provider_models()` and then to the provider module's constructor.

### Internal

- New `_validate_providers_registry()` in `amplifier_agent_lib/config/loader.py` enforces the closed schema for the new block.
- HTTP-face tests introduced from scratch under `tests/http/` covering lifespan boot scenarios and chat-completions validation.

### Migration

For server-mode users on `<= 0.8.0`: add a `providers` block to your `host_config.json`. Minimum to keep working with just Anthropic:

```json
{
  "providers": {
    "anthropic": {}
  }
}
```

Multi-provider example:

```json
{
  "providers": {
    "anthropic": {},
    "openai":    {"config": {"base_url": "https://api.openai.com/v1"}}
  }
}
```

If you don't pass `host_config.providers`, the server will exit at boot with a clear error message rather than running in a broken half-state.

## [0.8.0] — 2026-06-20

Adds an OpenAI-compatible chat-completions HTTP face for embedding amplifier-agent in third-party tools (opencode and similar), a persistent `auth` subcommand for provider credentials, and integrates the model-routing matrix for per-provider model selection. Existing JSON-RPC wire protocol unchanged — no wrapper bump required.

### Added

- **OpenAI-compatible chat-completions HTTP face** (`amplifier-agent serve chat-completions`). Exposes `/v1/models` and `/v1/chat/completions` over HTTP with bearer-token auth (`Authorization: Bearer ...`). Streams responses, returns OpenAI-shape envelopes, and supports multi-provider routing: the model field on each request is resolved through the served-models registry to the upstream provider, so a single server can serve Anthropic, OpenAI, Azure, and Ollama models from one endpoint. Enables direct integration with opencode (via the separate [`amplifier-app-opencode`](https://github.com/microsoft/amplifier-app-opencode) wrapper) and any other OpenAI-compatible client.

- **`amplifier-agent auth` subcommand** for persistent provider credentials. Stores at `~/.amplifier-agent/credentials.json` (mode `0600`) via the `set / list / remove / status / clear` actions. Resolution chain is **env-first**: shell env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) always win over the file, so existing shell-rc workflows are unchanged. The file lets users configure credentials once and have every subsequent invocation pick them up automatically — including the HTTP server, the `models list` command, and wrappers like `amplifier-opencode`. UX matches `claude login` / `gh auth login` / `aws configure` without the OAuth ceremony.

- **Host-tool delegation** over the chat-completions wire face. Tools declared by the host (in `host_config.json` under `host_tools`) are surfaced to the model with stub schemas; when the model invokes one, amplifier-agent emits a signal tool_call back to the client (carrying the same `chunk_id`), the client executes the tool host-side, and the result is returned for the model to continue. Lets the host own filesystem, shell, browser, or any custom tool without amplifier-agent having to bundle it.

- **Model routing matrix integration** (#64). The routing matrix can declare per-role provider/model preferences; amplifier-agent resolves the right provider per turn based on the matrix. Used by the new HTTP face for cross-provider model dispatch.

- **`X-Client-Session-Id` request header** for workspace correlation. Wrappers pass their own session ID; the server uses it as the workspace name when writing transcript logs, so client-side and server-side session bookkeeping stay aligned.

### Changed

- **Lifespan provider initialization** now iterates `KNOWN_PROVIDERS` and registers every provider whose module is installed AND whose credentials are present. Previously the chat-completions face hardcoded `inject_provider("anthropic")` at lifespan; injection is now per-request based on the model the client picks. Boot log surfaces a line per skipped provider (`"Skipping provider 'openai' -- module not installed"`, `"Skipping provider 'ollama' -- no credentials in env"`) so it's clear what amplifier-agent thinks it can serve.

- **`/v1/models` response** surfaces a `_provider` tag per model so OpenAI-compatible clients can see which provider serves each entry. Standard clients ignore the non-standard field per the OpenAI spec; aware clients can use it for routing decisions or display.

- **Usage-counter telemetry** in chat-completions responses now correctly reflects the provider that actually served the turn (was previously misattributed when routing across providers).

- **Bundle preparation pipeline** refactored to support the new HTTP face cleanly — same cache key semantics, no migration required.

### Internal

- `_resolve_env_credential` in `provider_sources.py` extended to chain env → `credentials.json` → empty. Lazy-imports the file reader from `admin/auth.py` to avoid a module-load cycle.
- New `admin/auth.py` (~330 lines) implements the `auth` subcommand surface: atomic JSON write (mode 0600), parent dir mode 0700, versioned envelope `{version: 1, providers: {...}}`, schema-tolerant load that round-trips unknown providers/fields.
- `routes/chat_completions.py` looks up the requested model in `app.state.served_models_registry` and passes the resolved `provider_id` + `upstream_model` through to `_session_runner.run_chat_turn` for per-request `inject_provider` under the existing `_create_session_lock` (save-restore pattern).
- `routes/models.py` surfaces `_provider` in the `/v1/models` response.

### Wire protocol

- Existing JSON-RPC wire face unchanged at `0.3.0`. **No wrapper bump required.** TypeScript wrapper stays at `0.7.0`, Python wrapper stays at `0.3.0`.
- New OpenAI-compatible chat-completions face is a separate wire — independent versioning is not currently surfaced; the schema is the OpenAI chat-completions subset documented in `README.md`.

### Migration

- No breaking changes for users on the JSON-RPC wire (existing `run`, `serve`, `models list`, etc. unchanged).
- New users / new integrations: prefer the chat-completions face for OpenAI-compatible clients; prefer `amplifier-agent auth set` over shell-rc exports for the "set once, works everywhere" UX. Both env vars and the file still work side-by-side.

## [0.7.0] — 2026-06-17

Built-in bundle replaced with vendored behavioral-anchor. Agent set, tool roster, and bundle name all change. Wire protocol unchanged — no wrapper bump required.

### Changed

- **Built-in bundle replaced with `amplifier-agent-behavioral-anchor`** (was `amplifier-agent-builtin`, v1.3.0). Adapted from the experimental `behavioral-anchor` bundle in `amplifier-foundation@main:experiments/behavioral-anchor/` with five amplifier-agent-specific modifications (see below). Manifest text + 6 agent definitions + `context/system.md` are vendored inside the wheel.

- **New sub-session agent set**: `architect`, `builder`, `debugger`, `git-ops`, `researcher` (plus retained `explorer`). Replaces the previous set of `planner`, `coder`, `tester`. **Breaking for users who scripted `delegate(agent="planner"|"coder"|"tester", ...)`** — those names are no longer recognized.

- **Tool roster expanded**: `tool-web` (web_search, web_fetch), `tool-apply-patch`, `tool-mode`, `tool-recipes`, `hooks-mode` added. Existing `tool-mcp`, `tool-skills`, `tool-todo`, `tool-delegate`, `tool-bash`, `tool-filesystem`, `tool-search`, `hooks-redaction`, `hooks-status-context`, `hooks-todo-reminder`, `hooks-session-naming`, `hook-context-intelligence` retained.

- **System prompt structure** is principle-led — a short set of named behavioral principles loaded once at the head via vendored `context/system.md`. Per-agent definitions are intentionally lean (no per-agent `tools:` blocks); sub-agents inherit the parent's tool roster through `tool-delegate.context_inheritance.enabled: true`.

### Amplifier-agent-specific modifications from upstream behavioral-anchor

| Upstream behavioral-anchor | This release | Why |
|---|---|---|
| no `default_provider` | `default_provider: anthropic` | Engine reads directly from frontmatter |
| `behaviors/streaming-ui.yaml` include | omitted | stdout reserved for JSON envelope (invariant #5); engine handles streaming via `bundle/hook_streaming.py` |
| `hooks-todo-display` | omitted | same stdout-contract reason |
| `behaviors/logging.yaml` include | `hook-context-intelligence` instead | preserves workspace JSONL alignment with amplifier-app-cli (per PR #57) |
| `hooks-approval` | omitted | no wire-protocol approval round-trip yet — would deadlock on policy-driven rules |
| no `tool-mcp` | added | preserves MCP support and `doctor` checks for existing users |

### Internal

- `AGENTS.md` gains a "Common pitfalls" entry on stale-cache troubleshooting. Foundation's source resolver *does* follow transitive deps declared in upstream module `pyproject.toml`s — but only when given a fresh git clone. Early bundle-swap failures (`No module named 'aiohttp'`, `No module named 'context_intelligence'`) were all stale-cache problems, not real install gaps. The existing `mcp` entry in our `pyproject.toml` may be vestigial.
- All `pyproject.toml` `force-include` entries updated to match the new agent + context filenames.
- Test suite rewritten/updated across 7 files for the new agent set; lean parameterized tests replace the previous per-old-agent body-section assertions.

### Engine compatibility

- Requires Python `>=3.12` (unchanged).
- Wire protocol: `0.3.0` (unchanged). **No wrapper bump required.**
- Bundle cache key (`sha256(bundle.md)`) changes — existing prepared pickles auto-invalidate. First run after upgrade does a cold-prep (~30–90s; larger module set than 0.6.0).

### Migration

- Scripts or wrapper code that delegates by agent name must be updated to the new set: `planner`→`architect`, `coder`→`builder`, `tester`→`debugger`. The retained `explorer` agent is unchanged in name (lean version of the definition).
- `default_provider: anthropic` is unchanged. No host-config migration.
- First run will re-prepare the bundle and re-install modules. Budget 30–90s.

## [ts-wrapper 0.6.2] — 2026-06-08

### Fixed

- **Wall-clock timeout is now opt-in.** Previously, `timeoutMs: undefined` silently inherited a 10-minute `DEFAULT_TIMEOUT_MS` cap inside `SessionHandle.submit()` — long agent turns (>600s) were killed with a synthesized `engine_hung` error and SIGTERM/SIGKILL. The new contract: the wall-clock hang timer is armed only when `timeoutMs` is a positive number. `undefined`, `0`, or any negative value disables it entirely.
- The Amplifier CLI itself imposes no per-turn timeout, so the wrapper SDK no longer does either. Callers that want the legacy cap can opt in explicitly with `timeoutMs: DEFAULT_TIMEOUT_MS` (now exported from the package).
- Real-world impact: agent tasks in Paperclip (and any other consumer) that legitimately ran past 10 minutes will no longer be killed mid-work.

### Added

- **`DEFAULT_TIMEOUT_MS` is now exported** from the package root, so callers that want the original 10-minute cap can opt in with `timeoutMs: DEFAULT_TIMEOUT_MS`.

### Tests

- New unit cases `(k) timeoutMs: 0` and `(l) timeoutMs: undefined` in `test/session-subprocess.test.ts` — 300ms windows confirm no `engine_hung` is synthesized.
- New `test/timeout-longwindow-integration.test.ts` — three end-to-end cases through the public `spawnAgent() → submit()` API against a real ~12s mock-engine subprocess: (1) `timeoutMs: 0` completes normally with no `engine_hung`, (2) `timeoutMs: undefined` same, (3) positive control `timeoutMs: 500` proves the timer still arms and cancels correctly.
- Full suite: 101/101 passing under `bun run test`; typecheck clean.

### Known issue

- With the wall-clock timer opt-in, callers that pass `0` or `undefined` get no wrapper-side hang detection. The 2s activity ticker emits heartbeats but does not escalate. A future iteration will add progress-based detection (`stuckDetection` config) so genuinely-hung subprocesses are recovered without re-introducing a wall-clock cap. Tracked in `ISSUES.md` as ISSUE-002.

### Engine compatibility

- Wire protocol: `0.3.0` (unchanged).

## [0.5.0] - 2026-06-03

New `update` subcommand for self-management + delegate sub-session approval/display inheritance fix.

### NEW

- **`amplifier-agent update` subcommand** — wraps the previously-required `uv tool install --reinstall --force "git+https://...@v<tag>"` ritual behind a single command:
  - No args: check latest GitHub Release, install if newer
  - `--check`: status-only, no install
  - `--tag <ref>`: install a specific tag/branch/SHA (`v0.4.0`, `main`, etc.)
  - `--force`: reinstall even when versions match (clears corrupted installs)
  - `--output json`: structured envelope for tooling
  - Detects install method (`uv tool` vs editable vs other) and refuses operations that would clobber a dev checkout

- **Engine bump 0.4.1 → 0.5.0**: additive feature (new subcommand) + delegate sub-session inheritance fix. No wire-protocol change. No wrapper version bump.

### Fixed

- **Side-effecting tool calls in `delegate` sub-sessions no longer auto-deny when the parent is configured with `-y` / `approval.mode: "yes"`.** Surfaced by a consumer report. Root cause: parent's approval provider was registered via `coordinator.register_capability("approval.request", ...)` (the capability registry), but `spawn_sub_session` was reading `parent.coordinator.approval_system` (a separate Rust-backed property slot). The two slots were uncoupled, so the child session inherited a `None` approval provider and hooks-approval auto-denied every tool that needed approval. Now `spawn.py` explicitly copies the `approval.request` and `display.emit` capabilities from parent to child after the child's session has mounted, restoring the inherit-policy semantics consumers expect.
- **Sub-session display events.** Same structural bug affected `display.emit` — sub-session events (token streams, tool/started, tool/completed) were silently dropped because parent registered via capability registry but spawn read from `coordinator.display_system`. Now both capabilities propagate. Consumers using `display.onEvent` (PR #36 / wrapper 0.6.1) on sub-session events will see them flow through correctly.

### Internal

- Followed `self-managing-tool-patterns` skill conventions for the update mechanism.
- API call to GitHub Releases is best-effort with clear failure messaging — no cached fallbacks.

### Engine compatibility

- Requires Python `>=3.12` (unchanged).
- Wire protocol: `0.3.0` (unchanged).

## [ts-wrapper 0.6.1] — 2026-06-03

### Fixed

- **`test/transport.test.ts > terminate() resolves with SIGTERM signal or non-zero exit code` flaked on CI with `Error: Test timed out in 5000ms`.** The test exercises actual subprocess SIGTERM handling, which is slower on Ubuntu runners than on local macOS. Per-test timeout bumped to 15s. Same class of fix as `#19 fix(wrapper): bump vitest testTimeout to 15s for CI transport test` from a prior release window.

### Why this didn't ship as part of 0.6.0

The 0.6.0 publish workflow run failed at the Test step before reaching `npm publish`. `amplifier-agent-ts@0.6.0` was never published. This 0.6.1 release supersedes that aborted attempt; consumers can install 0.6.1 directly without first installing 0.6.0.

### Released

- `amplifier-agent-ts` (TypeScript wrapper) 0.6.1

## [0.4.1] - 2026-06-03

### Fixed

- **uv workspace declaration referenced non-existent directories.** `pyproject.toml` declared `[tool.uv.workspace] members = ['packages/amplifier-agent', 'packages/amplifier-agent-session-spawner', 'wrappers/python']`, but the two `packages/...` directories have never existed in the repository. Most uv versions handle this gracefully (warn or silently ignore), but specific uv-version + config combinations would resolve the workspace install to an ancestor commit where pre-PR-#27 packaging bugs were still present, producing confusing hatchling errors at `uv tool install` time. Now declares only the real `wrappers/python` member.

### Migration

Consumers who hit `uv tool install` failures with `v0.4.0` should retry with `v0.4.1`. No code changes are needed on the consumer side.

### Credits

Surfaced by a consumer report against `v0.4.0`.
## [ts-wrapper 0.6.0] — 2026-06-03

Wrapper hardening release closing 8 consumer-reported gaps at 0.5.0.

### NEW

- **`SpawnAgentParams.configPath?: string`** (#1) — surface engine's `--config <path>` flag and `host_config.json` resolution to TS callers (engine side: PR #27 / v0.4.0; wrapper side: this release).
- **`SpawnAgentParams.runChildProcess?: ChildProcessFactory`** (#3) — injection point for substituting `child_process.spawn` (testability, sandboxing). `ChildProcessFactory` exported `@public`.
- **`SpawnAgentParams.approval?: { mode: 'yes' | 'no' | 'prompt' }`** (#10) — wires to engine `-y` / `-n` argv. `'prompt'` emits no flag and lets the engine fall back to `host_config.approval.mode` (PR #34) or the bundle's TTY-based default. The legacy `{ onRequest, timeoutMs }` shape still throws `approval_not_supported_in_v1` — Mode A has no mid-turn channel.
- **`SpawnAgentParams.allowProtocolSkew?: boolean`** (#9) — bypass the wrapper-side protocol-version check. Mirrors the engine's `host_config.allowProtocolSkew` knob.
- **Stderr NDJSON event pipeline** (#2, #4, #6) — `parseNdjsonStream` extracted as a standalone `@public` helper and wired onto the child subprocess's stderr stream inside `SessionHandle`. The 9 wire event types emitted by the engine (progress, result/delta, result/final, thinking/delta, thinking/final, tool/started, tool/completed, approval/request, approval/timeout, plus wire-level error) are parsed into a new `{type:'notification', method, params}` `DisplayEvent` variant and dispatched to `display.onEvent`. Previously stderr was buffered as raw text and `display.onEvent` was silently dropped.
- **`getEngineInfo()` implementation** (#7) — `engineVersion` populated from the `amplifier-agent version --json` probe that `spawnAgent()` now runs at init. `bundleDigest` populated from the same payload when present (forward-compatible — engine currently omits it; will populate automatically when a future engine release exposes it).
- **`checkProtocolVersion()` wired into init path** (#9) — wrapper-side fast-fail on protocol-version skew before subprocess spawn. Previously the utility existed but was never called.
- **Re-exports from `index.ts`** (#5) — `assembleArgv`, `AssembleArgvInput`, `resolveMcpConfigPath`, `cleanupSpillFile`, `McpSpillResult`, `buildEnv`, `resolveBinaryPath`, `probeEngineVersion`, `DEFAULT_ALLOWLIST`, `BLOCKED_ENV_KEYS`, `Transport`, `TransportOptions`, `ExitInfo`, `parseNdjsonStream`, `ParseNdjsonStreamOptions`, `checkProtocolVersion`, `VersionCheckResult`, `parseRunOutput`, `STDERR_TAIL_BYTES`, `SubprocessOutcome`, `makeApprovalHandler`, `ApprovalAdapter`, `ApprovalRequest`, `ApprovalHandler`, `ChildProcessFactory` — all annotated `@public`.
- **`PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`** bumped `"0.2.0"` → `"0.3.0"` to match the engine's current wire protocol. The previous pin was stale; the new `checkProtocolVersion()` wiring would have surfaced this at startup.

### BREAKING

- **`display.onEvent` now actually fires.** (#4) Callers that registered the callback expecting it to be a no-op may see new event flow. The `DisplayEvent` discriminated union has a new `notification` variant; exhaustive switch statements on `event.type` need a corresponding branch.
- **`SpawnAgentParams.approval` is now a union shape.** (#10) Callers passing `{ mode }` no longer hit `approval_not_supported_in_v1`. Callers that defensively caught that error when passing `mode` need to remove the try/catch.
- **`PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` value changed.** (#9) Wrappers pinned at `"0.2.0"` will fail-fast against engines speaking `"0.3.0"` rather than discovering the mismatch at first `submit()`. This is wrapper-internal; the engine already requires `"0.3.0"` since 0.4.0.
- (Minor) The re-export surface of `index.ts` is now larger (#5). Callers that relied on the previously-implicit "these aren't public" assumption may see new TypeScript completion entries.

### Fixed

- Stderr event loss (#2)
- `display.onEvent` silent drop (#4)
- `Transport` dead code (#6 — root cause of #2/#4)
- No `configPath` plumbing (#1, wrapper side)
- No `runChildProcess` injection (#3)
- Missing public re-exports (#5)
- `getEngineInfo()` Task-9 TODO (#7)
- `checkProtocolVersion()` not called (#9)
- Approval API stub (#10)

### Not changed (clarification for the consumer report)

- `InitializeParams.mcpConfigPath` wire-protocol field is **intentionally retained** in protocol-0.3.0. The engine still reads it via `handle_initialize` → `AMPLIFIER_MCP_CONFIG`. Only the `--mcp-config-path` argv flag was removed (PR #29). The TS type (auto-generated from `schemas/InitializeParams.schema.json`) correctly reflects this and was not modified.

### Engine compatibility

- Requires `amplifier-agent >= 0.4.0` (host config layer + `approval.mode` config key).
- Pinned protocol: `0.3.0`.

### Released

- `amplifier-agent-ts` (TypeScript wrapper) 0.6.0

## [0.4.0] — 2026-06-03

### BREAKING

**Engine argv surface removed:**
- `--host-capabilities` (#27) — write-only, zero read sites
- `--env-allowlist`, `--env-extra` (#27) — subsumed by host config layer
- `--allow-protocol-skew` + `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` env var (#27) — moved to host config `allowProtocolSkew: true`
- `--mcp-config-path` (#29) — subsumed by `mcp.configPath` host-config key + `$AMPLIFIER_MCP_CONFIG` env var
- `--skills-dir` (#30) — subsumed by `skills:` host-config key + `$AMPLIFIER_SKILLS_DIR` env var

**CLI behavior changes:**
- **CLI (BREAKING)** `--skills-dir` argv flag removed from `amplifier-agent run`. Migration paths (per D13):
  1. **Preferred — env var**: set `$AMPLIFIER_SKILLS_DIR` (preserved as the adapter-bridge surface). The `tool-skills` module continues to honour it.
  2. **Or — host_config**: add a `skills:` block to your host_config JSON (per D11) and pass it via `--config <path>` or `$AMPLIFIER_AGENT_CONFIG`. Example:
     ```json
     {
       "skills": {
         "skills": ["/path/to/extra/skills"],
         "visibility": {"max_skills_visible": 20}
       }
     }
     ```
- **CLI (BREAKING — G3)** Headless `amplifier-agent run` invocations (non-TTY stdin) now **fail fast at startup** when neither `-y` / `-n` nor `host_config.approval.mode` declares an explicit approval policy (#34). The previous behavior — silently defaulting to `approval.mode='no'` and producing success-shaped no-op runs in which every tool call was auto-denied — was indefensible: monitoring saw green, the agent appeared to succeed, and zero work happened with no programmatic signal to catch it. The new behavior writes a §4.1 error envelope (`code: approval_unconfigured`, `classification: protocol`) and exits 2 with a remediation line pointing at the three escape hatches. Migration: pass `-y` (auto-approve), `-n` (explicit auto-deny), or set `{"approval": {"mode": "yes"|"no"|"prompt"}}` in `--config` / `$AMPLIFIER_AGENT_CONFIG`. Interactive runs from a TTY are unaffected — the default remains `prompt`.

**Wire surface removed (envelope + initialize):**
- `metadata.hostCapabilities` from response envelope (#27)
- `InitializeParams.host` (#27)
- `InitializeParams.mcpServers` renamed to `mcpConfigPath` (PR #24, prior release window)

**Wire protocol bumped:** `0.2.0` → `0.3.0`. Old wrappers fail handshake with `protocol_version_mismatch`, exit 2 (intentional).

**Wrapper API removed (TS + Python parity):**
- `SpawnAgentParams.host` / `HostCapabilities` type / `InitializeHostParams` type (#27)
- `mcpConfigPath` field + argv emission (#29) — wrappers now inject `AMPLIFIER_MCP_CONFIG` env var
- `envAllowlist` / `envExtra` / `allowProtocolSkew` fields + argv emission (#31)

### NEW

**Host config layer (#27, #30, #34):**
- `--config <path>` argv flag + `$AMPLIFIER_AGENT_CONFIG` env var (2-tier resolution)
- 4 top-level config keys: `mcp`, `approval`, `provider`, `allowProtocolSkew`
- Pass-through schema mirroring downstream module configs
- Layered merge with bundle defaults at module mount time
- Strict-by-default validation
- `default_provider:` field in vendored `bundle.md`
- `amplifier-agent config show` reports resolved path + source + parsed values
- XDG resolution consolidated through `persistence.py`
- **`approval.mode` config key (#34, G3)** — values `"yes" | "no" | "prompt"`. Lets hosts that drive `amplifier-agent` via host_config (no argv access) express the same intent as CLI flags `-y` / `-n`. Validated at parse time (`config_invalid_type` on unknown values or non-strings). Precedence: argv flag > host_config > bundle default. `VALID_APPROVAL_MODES` exported for downstream policy validation.

**Engine dependency management (#34, G4):**
- `mcp` added as a declared transitive dependency in `pyproject.toml`. The canonical install command — `uv tool install git+https://github.com/microsoft/amplifier-agent` — now works out of the box. Hosts no longer need to know to pass `--with mcp`, and forgetting it no longer produces the downstream `'Bundle' object has no attribute 'origins'` AttributeError that masked the real cause.
- New doctor check `_check_mcp_importable()` — `amplifier-agent doctor` gains an `mcp module: importable` check that fires whenever `tool-mcp` is declared in `bundle.md`. Reports `[ OK ]`, `[FAIL]` with a clear remediation line, or `[INFO]` (skipped) if `tool-mcp` is not in the bundle. Catches the "forgot `--with mcp` on an old install" condition that the prior doctor passed silently.

**Skills block in host config (#30):**
- 5th top-level config key: `skills:` — pass-through to `tool-skills` module
- `skills.skills: list[str]` list-concatenated with bundle-declared sources (D12: bundle-first, host-appended)
- `skills.visibility: dict` dict-overlaid on bundle visibility defaults (D11)
- `tool-skills` module declared in vendored `bundle.md` (sourced from `git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills`) with three default skill sources (curated bundle, `.amplifier/skills`, `~/.amplifier/skills`)
- `amplifier-agent config show` reports post-merge `skills` block — bundle defaults plus host additions
- Bundle cache invalidates on upgrade (`bundle.md` sha256 changes) — run `amplifier-agent prepare` after upgrade

### Internal

- `provider_detect.py` deleted (vestigial)
- `src/amplifier_agent_cli/skill_sources.py` (`inject_skill_dirs()` helper) deleted — unreachable after `--skills-dir` removal
- `pyproject.toml` wheel-build duplicate-include fix (#27)
- Conformance suite restored to green + new baseline/skew-override fixtures (#32)
- `tests/test_phase_2_1_exit_gate.py` fixture-name fix (#32 side fix)
- `host_config` schema reference docs added — `docs/configuration.md` (#34, N1/N2). Authoritative reference for the closed top-level host_config schema, per-key semantics, precedence model (argv flag > host_config > bundle default), error codes (`approval_unconfigured`), and concrete examples for common host integrations.
- Test infrastructure (#34): `conftest.py` adds autouse fixture defaulting `is_stdin_tty` to True for all tests, plus a session-scoped fixture seeding `AMPLIFIER_AGENT_CONFIG` with `{approval:{mode:yes}}` for subprocess tests, so existing tests behave as TTY-attached by default and subprocess tests don't hit the new G3 headless check.

### Migration

- **Existing wrappers / hosts**: must drop the removed argv flags and wire fields. Mismatch is loud (`protocol_version_mismatch`, exit 2) — no silent downgrades.
- **Skills path consumers**: prefer `$AMPLIFIER_SKILLS_DIR` (preserved as adapter-bridge env var) or add a `skills:` block to host_config. The `--skills-dir` argv flag is gone.
- **MCP config path consumers**: prefer `$AMPLIFIER_MCP_CONFIG` env var or add an `mcp:` block to host_config. The `--mcp-config-path` argv flag is gone.
- **Headless / non-TTY callers (#34, G3)**: must declare approval intent explicitly. Either pass `-y` / `-n` on the command line, or set `{"approval": {"mode": "yes"|"no"|"prompt"}}` in `--config` / `$AMPLIFIER_AGENT_CONFIG`. Non-TTY runs without explicit policy now exit 2 with `approval_unconfigured`.

### Cross-repo follow-ups (NOT in this release)

Downstream consumers (notably `amplifier-module-provider-nc`) must catch up:
1. Drop `host: { capabilities }` from `spawnAgent` call (#27)
2. Migrate `--mcp-config-path` argv → `AMPLIFIER_MCP_CONFIG` env var injection (#29)
3. Stop passing `envAllowlist` / `envExtra` / `allowProtocolSkew` to `spawnAgent` (#31)

### Design references

- `docs/designs/2026-06-01-host-config-layer-revisit.md` (D11/D12/D13 — skills block)
- `docs/designs/2026-06-01-drop-host-capabilities.md`
- `docs/configuration.md` (host_config schema reference, G3 approval policy details — #34)

### Released

- `amplifier-agent` (engine) 0.4.0
- `amplifier-agent-client` (Python wrapper) 0.4.0
- `amplifier-agent-ts` (TypeScript wrapper) 0.5.0 — bumped past published 0.4.0 because the accumulated breaking API changes since 0.4.0 was published (PRs #27, #29, #30, #31) cannot be released as a patch or minor and 0.4.0 is already on npm.
- Wire protocol 0.3.0

## [0.3.0 engine / 0.4.0 wrapper] — 2026-05-27

### Fixed

- **Engine** `_runtime.py` — three latent runtime-crashing bugs in MCP server config handling, all silenced by `# pyright: ignore` suppressions:
  - `AttributeError: 'PreparedBundle' object has no attribute 'config'` — author wrote prose comments asserting `PreparedBundle.config` was the merged bundle yaml; it does not exist. The merged yaml lives on `mount_plan`.
  - `AttributeError: 'list' object has no attribute 'get'` — `mount_plan["tools"]` is a list of `{module, source, config}` dicts, not a dict keyed by module name. The author treated it as a dict.
  - `TypeError: PreparedBundle.create_session() got an unexpected keyword argument 'tool_overrides'` — the kwarg does not exist on the foundation API.
  Each suppression masked a real attribute or call error pyright had flagged. The whole `--mcp-servers` flow was non-functional at 0.2.0; the file-based discovery paths documented in `amplifier-module-tool-mcp` continued to work.

### Changed

- **Wire (BREAKING)** `PROTOCOL_VERSION` bumped `0.1.0` → `0.2.0`. MCP server delivery refactored from inline `mcpServers: dict` to path-based `mcpConfigPath: str`. The engine forwards the path to `tool-mcp` via `AMPLIFIER_MCP_CONFIG` (one of four documented config priorities in the module). Old wrappers fail with a clean `protocol_version_mismatch` rather than a confusing runtime crash.
- **Engine CLI** `--mcp-servers` flag renamed to `--mcp-config-path`. The engine no longer parses MCP config contents — it validates the path exists and forwards it to the module.
- **Wrapper** `mcp-spill.ts` now always spills to a `0600` tmpfile (dropping the inline-JSON-on-argv branch — also eliminates server-config visibility in `ps aux`) and writes content in the format the module expects (`{"mcpServers": <map>}`).
