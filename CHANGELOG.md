# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
