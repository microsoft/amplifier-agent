# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Engine** `skills:` block as the fifth top-level key in host_config (D11). Pass-through to the `tool-skills` module's `config`. Supports `skills.skills: list[str]` (list-concatenated with bundle-declared sources — D12, bundle-first, host-appended) and `skills.visibility: dict` (dict-overlaid on the bundle's visibility defaults — D11).
- **Bundle** `tool-skills` module declared in `bundle.md` (sourced from `git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills`) with three default skill sources (curated bundle, `.amplifier/skills`, `~/.amplifier/skills`) and default visibility config. Cache key invalidates on upgrade (`bundle.md` sha256 changes) — run `amplifier-agent prepare` after upgrade.
- **CLI** `config show` reports the post-merge `skills` block — bundle defaults plus host additions (D8), so operators can confirm both that host additions landed and that bundle defaults were not silently dropped.
- **Engine (G4)** `mcp` is now a declared transitive dependency in `pyproject.toml`. The canonical install command — `uv tool install git+https://github.com/microsoft/amplifier-agent` — now works out of the box. Hosts no longer need to know to pass `--with mcp`, and forgetting it no longer produces the downstream `'Bundle' object has no attribute 'origins'` AttributeError that masked the real cause.
- **Engine (G4)** `amplifier-agent doctor` gains an `mcp module: importable` check that fires whenever `tool-mcp` is declared in `bundle.md`. The check attempts `import mcp` and reports `[ OK ]`, `[FAIL]` with a clear remediation line, or `[INFO]` (skipped) if `tool-mcp` is not in the bundle. Catches the "forgot `--with mcp` on an old install" condition that the prior doctor passed silently.
- **Config (G3)** `approval.mode` is now a recognized sub-key under `host_config.approval`. Accepts one of `{"yes", "no", "prompt"}` — the same three values `CliApprovalSystem` accepts. Lets hosts that drive `amplifier-agent` via host_config (no argv access) express the same intent as `-y` / `-n` / TTY-prompt without depending on argv flags. The loader validates the value at parse time (`config_invalid_type` on unknown values or non-strings).
- **Docs** New `docs/configuration.md` — authoritative reference for the closed top-level host_config schema, per-key semantics, precedence model (argv flag > host_config > bundle default), error codes, and concrete examples for common host integrations.

### Changed

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
- **CLI (BREAKING — G3)** Headless `amplifier-agent run` invocations (non-TTY stdin) now **fail fast at startup** when neither `-y` / `-n` nor `host_config.approval.mode` declares an explicit approval policy. The previous behavior — silently defaulting to `approval.mode='no'` and producing success-shaped no-op runs in which every tool call was auto-denied — was indefensible: monitoring saw green, the agent appeared to succeed, and zero work happened with no programmatic signal to catch it. The new behavior writes a §4.1 error envelope (`code: approval_unconfigured`, `classification: protocol`) and exits 2 with a remediation line pointing at the three escape hatches. Migration: pass `-y` (auto-approve), `-n` (explicit auto-deny), or set `{"approval": {"mode": "yes"|"no"|"prompt"}}` in `--config` / `$AMPLIFIER_AGENT_CONFIG`. Interactive runs from a TTY are unaffected — the default remains `prompt`.

### Removed

- **Engine** `src/amplifier_agent_cli/skill_sources.py` (the `inject_skill_dirs()` helper). Unreachable after `--skills-dir` removal.

### Design references

- `docs/designs/2026-06-01-host-config-layer-revisit.md` (D11/D12/D13)
- `docs/configuration.md` (host_config schema reference, G3 approval policy details)

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
- **Wrapper** `resolveMcpServersFlag` → `resolveMcpConfigPath`; `McpSpillResult.flag` → `configPath`; `mcpServersFlag` in `AssembleArgvInput` → `mcpConfigPath`.
- **Engine** audit field renamed `mcpServersDigest` → `mcpConfigPathDigest` (hashes the path string for stable identifier without dragging file IO into the audit path).

### Architecture

Each layer now owns exactly one responsibility:

| Layer | Responsibility |
|---|---|
| Wrapper | Write the file in the format the module expects; manage tmpfile lifecycle |
| Engine CLI | Validate that the path exists; forward to runtime |
| Engine runtime | One line: `os.environ["AMPLIFIER_MCP_CONFIG"] = mcp_config_path` |
| Module (`amplifier-module-tool-mcp`, unchanged) | Read the file via its existing 4-source config priority |

The previous design tried to merge MCP config inside the engine via a non-existent `tool_overrides` kwarg, building parallel rails to a config-discovery mechanism the module already implemented.

### Released

- `amplifier-agent` (engine) 0.3.0 — consumers pin via `git+https://github.com/microsoft/amplifier-agent@engine-v0.3.0`. PyPI publishing not yet wired.
- `amplifier-agent-ts` (wrapper) 0.4.0 — published to npm with provenance via the existing OIDC trusted-publishing workflow (`publish-wrapper.yml` on `wrapper-v*` tag push).

### Migration

- **Wrapper callers**: no API change. `SpawnAgentParams.mcpServers: Record<string, McpServerConfig>` is preserved. The wire-level rename is internal to the wrapper.
- **Container/Dockerfile consumers (e.g. nanoclaw)**: bump `AMPLIFIER_AGENT_REF` to `engine-v0.3.0` and bump the wrapper dependency to `0.4.0`. The pair must move together — protocol mismatch errors are explicit and ship with remediation hints.

## [0.2.0] — 2026-05-22

### Added

- **Wire** (`mcpServers`, `host.capabilities`, `HostCapabilities` / `McpServerConfig` interfaces) (A1)
- **Wire** (`severity`, `correlationId`, `stderrTail` on `AaaError`; `classification: approval` enum) (A1)
- **Engine** `session_store.py` `SessionStore` (JSONL transcript + JSON metadata, atomic writes via `write_with_backup`) (A2)
- **Engine** `incremental_save.py` `IncrementalSaveHook` (tool:post priority 900, flushes after every tool call) (A2)
- **Engine** `wire_approval_provider.py` `WireApprovalProvider` (three-code error contract: `approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`) (A3)
- **Engine** `_runtime.py` (resume path via `SessionStore`, approval shim, MCP threading via `tool_overrides`, host capabilities storage) (A2, A3, A5)
- **Bundle** `context-simple` replaces `context-persistent` (CR-1), `tool-mcp@main` and `hooks-approval@v0.1.0` added, `hooks-logging` removed (SC-2), bundle version `1.2.0` (A4)
- **CLI** `doctor --strict` exits non-zero on any warning (CI gate), `--quick` minimal check, `--emit-sha` bundle SHA baseline; new checks: bundle module presence, `wire_approval_provider` shape, `session_store` roundtrip (A7)
- **Conformance** four new scripted-replay fixtures (`initialize-with-mcpservers`, `initialize-with-host-capabilities`, `approval-shim-three-error-codes`, `resume-with-session-store`), parity lint green on all 9 fixtures in TS and Py (A8)
- **Wrappers** `BLOCKED_ENV_KEYS` validation rejects `PYTHONPATH` / `LD_PRELOAD` / `LD_LIBRARY_PATH` / `PYTHONSTARTUP` / `PATH` / `PYTHONHOME` / `PYTHONNOUSERSITE` / `DYLD_INSERT_LIBRARIES` / `DYLD_LIBRARY_PATH` with `AaaError(code='env_injection_rejected')` (A6)
- **Wrappers** `probeEngineVersion()` made `async` in both TS and Py (A6)

### Changed

- **Wire** `PROTOCOL_VERSION` bumped `"2026-05-aaa-v0"` → `"0.1.0"` (breaking change, both ends strict-refuse) (A1)
- **Bundle** `bundle.version` `1.1.0` → `1.2.0` (cache invalidated; run `amplifier-agent prepare` after upgrade) (A4)

### Removed

- **Bundle** `hooks-logging` module removed (session audit now handled by `IncrementalSaveHook` to host-mounted volume) (A4, SC-2)

### Design references

- `docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md`
- `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md`
- `docs/designs/2026-05-19-baked-in-bundle-decision.md`

## [0.0.1] — 2026-05-20

Initial implementation. Protocol, engine, bundle, wrapper stubs. Not production-ready.
