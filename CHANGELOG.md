# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [ts-wrapper 0.6.1] — 2026-06-03

### Fixed

- **`test/transport.test.ts > terminate() resolves with SIGTERM signal or non-zero exit code` flaked on CI with `Error: Test timed out in 5000ms`.** The test exercises actual subprocess SIGTERM handling, which is slower on Ubuntu runners than on local macOS. Per-test timeout bumped to 15s. Same class of fix as `#19 fix(wrapper): bump vitest testTimeout to 15s for CI transport test` from a prior release window.

### Why this didn't ship as part of 0.6.0

The 0.6.0 publish workflow run failed at the Test step before reaching `npm publish`. `amplifier-agent-ts@0.6.0` was never published. This 0.6.1 release supersedes that aborted attempt; consumers can install 0.6.1 directly without first installing 0.6.0.

### Released

- `amplifier-agent-ts` (TypeScript wrapper) 0.6.1

## [0.5.1] - 2026-06-03

### Fixed

- **Side-effecting tool calls in `delegate` sub-sessions no longer auto-deny when the parent is configured with `-y` / `approval.mode: "yes"`.** Surfaced by a consumer report. Root cause: parent's approval provider was written to `coordinator.register_capability("approval.request", ...)` (capability registry), but `spawn_sub_session` was reading `parent.coordinator.approval_system` (a separate Rust-backed property slot). The two slots were uncoupled, so the child session inherited a `None` approval provider and hooks-approval auto-denied every tool that needed approval. Now `spawn.py` explicitly copies the `approval.request` and `display.emit` capabilities from parent to child after the child's session has mounted, restoring the inherit-policy semantics consumers expect.

### Consumer impact

Workflows that use `delegate` to spawn sub-agents (e.g., calling `bash`, `file_write`, or any other side-effecting tool from a delegated agent) are now unblocked. Previously every such call hit `"No approval provider registered, auto-denying"` despite the parent process being explicitly configured to auto-approve.

### Side fix

- **Sub-session display events.** Same structural bug affected `display.emit` — sub-session events (token streams, tool/started, tool/completed) were silently dropped because parent registered via capability registry but spawn read from `coordinator.display_system`. Now both capabilities are propagated. Consumers using `display.onEvent` (PR #36 / wrapper 0.6.1) on sub-session events should see them flow through correctly.

### Migration

None. Pure bug fix — no API changes, no flag changes, no wire-protocol changes. Consumers on 0.5.0 (once PR #38 lands) or earlier engine versions can upgrade to 0.5.1 with no code changes on their side.

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
