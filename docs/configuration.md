# Host Configuration Reference

This document is the authoritative reference for the `host_config` JSON file consumed by `amplifier-agent`. It covers the closed top-level schema, per-key semantics, merge precedence, error codes, and concrete examples for common host integrations.

> **Audience.** Adapter authors and host integrators. If you are running `amplifier-agent` interactively from a terminal, you probably do not need a host config file at all — the bundle defaults plus `-y` / `-n` argv flags cover the common interactive cases.

---

## How host config is loaded

The CLI resolves host configuration in this order (first present wins):

1. **`--config <path>`** argv flag on `amplifier-agent run`.
2. **`$AMPLIFIER_AGENT_CONFIG`** environment variable.
3. **None.** When neither is set, the engine falls back to the vendored bundle defaults and argv flags only.

When tier 1 or tier 2 is set, the file must exist and parse as JSON. A `null` literal at the document root is normalized to `{}`. A missing file or unreadable path raises `ConfigError(code='config_unreadable')` with exit code 2 — setting the env var is treated as an affirmative declaration, not an optional hint.

```bash
# Two equivalent invocations:
amplifier-agent run "hello" --config /etc/amplifier/host.json
AMPLIFIER_AGENT_CONFIG=/etc/amplifier/host.json amplifier-agent run "hello"

# When both are set, --config wins.
```

---

## Top-level schema (closed)

The top-level schema is **closed**: exactly five keys are valid, and any unknown top-level key raises `ConfigError(code='config_unknown_key')`. This is a deliberate trade — it costs hosts one round of "did I misspell `aproval`?" feedback once, and prevents an entire class of silent-typo failures going forward.

| Key | Type | Purpose |
|---|---|---|
| `mcp` | object | Host-side MCP server configuration. Overlays onto the `tool-mcp` module's mount config. |
| `approval` | object | Host-side tool-call approval policy. Overlays onto the `hooks-approval` module's mount config; also feeds the CLI-layer approval-mode resolver (see G3 below). |
| `provider` | object | Selects which provider module to mount and overlays the provider module's config. |
| `allowProtocolSkew` | boolean | Bypasses the strict `--protocol-version` self-check. Unsafe; for development only. |
| `skills` | object | Host-supplied skill sources and visibility overrides; merged into the `tool-skills` module's mount config. |

Inner shapes are intentionally less strict than the top level — the loader validates structural invariants (types, allowed sub-keys for some blocks) and otherwise passes the value through to the downstream module, which owns its own key vocabulary and evolves independently of the engine.

---

## Precedence model

For every concept the engine consumes — provider selection, approval policy, MCP config, skills — three sources may contribute. They resolve in this order:

| Tier | Source | Wins over | Example |
|---|---|---|---|
| 1 | **Argv flag** | host_config, bundle default | `--provider openai`, `-y`, `-n` |
| 2 | **host_config**  | bundle default | `{"provider": {"module": "openai"}}`, `{"approval": {"mode": "yes"}}` |
| 3 | **Bundle default** | (lowest) | `default_provider: anthropic` in `bundle.md` |

This uniform precedence is the principle the engine enforces everywhere: an argv flag is always a forceful override that cannot be silenced by config; host_config is the recommended persistent expression of host intent; bundle defaults are the floor.

**Argv flags relevant to this precedence model:** `--provider`, `-y` / `--yes`, `-n` / `--no`. Everything else flows through host_config or env vars.

---

## Per-key reference

### `mcp`

Overlays the `tool-mcp` module's config. The most common use is pointing the engine at an MCP servers JSON file managed by the host.

| Sub-key | Type | Effect |
|---|---|---|
| `configPath` | string | Sets `$AMPLIFIER_MCP_CONFIG` for the engine's process; `tool-mcp` reads it natively. |

```json
{ "mcp": { "configPath": "/var/run/amplifier/mcp.json" } }
```

Hosts that prefer to set `$AMPLIFIER_MCP_CONFIG` directly on the subprocess env may omit the `mcp` block entirely.

> **Wire vs. CLI surface.** The `--mcp-config-path` argv flag was removed in PR #29. The wire-level `mcpConfigPath` field still appears in `InitializeParams.schema.json` for a future Mode B / stdio path; Mode A (CLI subprocess) does not use it.

### `approval`

Overlays the `hooks-approval` module's config *and* feeds the CLI-layer approval-mode resolver.

| Sub-key | Type | Effect |
|---|---|---|
| `mode` | string — one of `"yes"`, `"no"`, `"prompt"` | **G3.** Host-side equivalent of the `-y` / `-n` argv flags. Honored when no argv flag is present. See "Approval policy" below. |
| `patterns` | array of strings | Pattern list passed to `hooks-approval` for pattern-based matching. Each item must be a JSON string literal. |

Validation:

- `approval.mode` outside `{"yes", "no", "prompt"}` → `ConfigError(code='config_invalid_type')` at parse time, with the offending value and the valid set in the error message.
- `approval.patterns` containing a non-string item → `ConfigError(code='config_invalid_type')` naming the index of the offending item.
- Omitting `approval` entirely, or omitting `approval.mode`, lets argv / TTY behavior apply (see G3 below).

```json
{ "approval": { "mode": "yes" } }
```

### `provider`

Selects which provider module the engine mounts and overlays the provider's config.

| Sub-key | Type | Effect |
|---|---|---|
| `module` | string — one of `"anthropic"`, `"openai"`, `"azure-openai"`, `"ollama"` | Selects the provider module. Beats `default_provider` from `bundle.md` but is itself beaten by `--provider`. |
| `config` | object | Shallow-overlays onto the provider module's mount config. Provider modules read keys like `model`, `max_tokens`, `reasoning_effort` from here. |

Validation:

- `provider.module` outside the four valid module names → `ConfigError(code='config_invalid_provider_module')`.
- `provider.config` is a free-form object — keys are the provider module's responsibility, not the loader's.

```json
{
  "provider": {
    "module": "anthropic",
    "config": { "model": "claude-3-5-sonnet-20241022" }
  }
}
```

Model selection has no dedicated argv flag in the engine. Hosts that want to honor an operator-selected model use `provider.config.model`.

### `allowProtocolSkew`

Boolean; default `false`. When `true`, the CLI accepts a `--protocol-version <ver>` flag whose value does not match `PROTOCOL_VERSION`. Documented as unsafe; intended for development against a wrapper version that drifted from the engine.

```json
{ "allowProtocolSkew": true }
```

The legacy `--allow-protocol-skew` argv flag (and the `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` env var) were removed in PR #27 in favor of this host_config field.

### `skills`

Overlays the `tool-skills` module's config. See `CHANGELOG.md` [Unreleased] for the D11/D12/D13 design references and the bundle-first / host-appended merge semantics.

| Sub-key | Type | Effect |
|---|---|---|
| `skills` | array of strings | Additional skill source URIs. Bundle-declared sources go first; host entries append. |
| `visibility` | object | Shallow-overlays onto the bundle's visibility defaults (`enabled`, `inject_role`, `max_skills_visible`, …). Inner keys are pass-through. |

```json
{
  "skills": {
    "skills": ["/var/run/amplifier/host-managed-skills"],
    "visibility": { "max_skills_visible": 20 }
  }
}
```

Modes have no host-config key: they are activated per turn via the `--mode` argv flag, not through host config.

---

## Approval policy in headless runs (G3)

Headless callers (non-TTY stdin) must declare an explicit approval policy. Without one, `amplifier-agent run` exits 2 at startup with a structured envelope:

```json
{
  "error": {
    "code": "approval_unconfigured",
    "classification": "protocol",
    "message": "Headless run requires an explicit approval policy. ...",
    "remediation": "Pass `-y` to auto-approve, `-n` to auto-deny, or set `{\"approval\": {\"mode\": \"yes\"|\"no\"|\"prompt\"}}` in your --config / $AMPLIFIER_AGENT_CONFIG file."
  }
}
```

This replaces the prior silent default-deny behavior, which was the worst possible failure mode: every tool call silently denied, the run exiting 0 with a valid-looking JSON envelope, and the agent appearing to succeed while doing zero work. Hosts that genuinely want deny-all in headless runs must say so explicitly via `-n` or `approval.mode = "no"`.

Resolution order for the approval mode (matches the general precedence model above):

1. `-y` / `--yes` → `"yes"`
2. `-n` / `--no` → `"no"`
3. `host_config.approval.mode` → that value
4. TTY-attached stdin → `"prompt"`
5. Non-TTY with no policy → **fail-fast** (exit 2)

---

## Error reference

All loader errors raise `ConfigError`, which subclasses `AaaError`. The CLI's envelope path maps `classification: "protocol"` to exit code 2.

| Code | Cause |
|---|---|
| `config_unreadable` | The path resolved from `--config` or `$AMPLIFIER_AGENT_CONFIG` does not exist or cannot be read. |
| `config_malformed_json` | The file is not valid JSON, or the root value is not a JSON object. |
| `config_unknown_key` | A top-level key outside `{mcp, approval, provider, allowProtocolSkew, skills}` was present. |
| `config_invalid_type` | A typed sub-field has the wrong shape (e.g. `approval.mode` not in the valid set; `skills.skills` not a list of strings; `approval.patterns` containing a non-string). |
| `config_invalid_provider_module` | `provider.module` is not one of `{anthropic, openai, azure-openai, ollama}`. |
| `approval_unconfigured` | (Not a loader error; raised by the CLI at startup.) Headless run with no `-y`/`-n` and no `approval.mode` — see G3 above. |

---

## Examples

### Minimal headless run with default provider

```json
{ "approval": { "mode": "yes" } }
```

This is sufficient for a host that uses `--config` to express "auto-approve all tool calls" and lets the bundle's `default_provider` (currently `anthropic`) handle provider selection.

### Headless with explicit provider and model

```json
{
  "approval": { "mode": "yes" },
  "provider": {
    "module": "anthropic",
    "config": { "model": "claude-3-5-sonnet-20241022" }
  }
}
```

### Subprocess-host adapter (paperclip-style)

```json
{
  "approval": { "mode": "yes" },
  "provider": {
    "module": "anthropic",
    "config": { "model": "claude-3-5-sonnet-20241022" }
  },
  "mcp": { "configPath": "/var/run/paperclip/instances/<agent-id>/mcp.json" },
  "skills": {
    "skills": ["/var/run/paperclip/instances/<agent-id>/skills"]
  }
}
```

The adapter writes this file once per agent (atomic write, content-addressed or per-instance), passes `--config <path>` to the engine subprocess on every heartbeat, and lets the engine re-read it each turn.

### Strict deny-all for a test harness

```json
{ "approval": { "mode": "no" } }
```

Used when a test harness wants to assert "tools did not run" without depending on argv flags.
