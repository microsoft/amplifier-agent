# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
