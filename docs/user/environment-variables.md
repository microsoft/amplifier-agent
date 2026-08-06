# Environment variables

Every environment variable amplifier-agent reads, sorted by scope.

## amplifier-agent's own variables

| Variable | Purpose |
|---|---|
| `AMPLIFIER_AGENT_HOME` | Override the storage root. Default: `~/.amplifier-agent/`. Relocates `cache`, `config`, and `state` together. |
| `AMPLIFIER_AGENT_CONFIG` | Path to the host config JSON file. The `--config` flag overrides this. If set to a non-existent path, the run fails with `config_unreadable`. |
| `AMPLIFIER_AGENT_WORKSPACE` | Workspace slug for session isolation. The `--workspace` flag overrides this. Falls through to a deterministic slug derived from `--cwd` (basename + sha256 prefix) when unset. |
| `AMPLIFIER_AGENT_BIN` | (TypeScript SDK only) Path to the engine binary. The SDK checks this before `which amplifier-agent`. Useful in test harnesses and editable dev checkouts. |
| `AMPLIFIER_AGENT_DEBUG_SIDLOG` | If set (any value), emit `engine-sid-ok pid=<pid> sid=<sid>` to stderr at engine boot. Diagnostic only — used to confirm `setsid` ran. |
| `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` | (TypeScript SDK only) Boolean-ish flag; equivalent to `allowProtocolSkew: true` in host config. Mentioned in the protocol-mismatch remediation message. |

### `AMPLIFIER_AGENT_HOME`

```bash
export AMPLIFIER_AGENT_HOME=/var/lib/amplifier-agent
amplifier-agent config show | grep -A1 amplifier_agent_home
#   "amplifier_agent_home": {
#     "value": "/var/lib/amplifier-agent",
#     "source": "env:AMPLIFIER_AGENT_HOME"
#   }
```

Caveat: not every code path expands env vars in paths. The bundle's `hook-context-intelligence` writes its observability events to the literal path `~/.amplifier-agent/state/workspaces/...` (only `~` is expanded). If you relocate via `AMPLIFIER_AGENT_HOME`, transcripts and audits move with it; the context-intelligence events stay at the literal default path. This is an upstream hook limitation, not amplifier-agent behavior — for now, run from `~/.amplifier-agent/` if you need the observability events alongside transcripts.

### `AMPLIFIER_AGENT_CONFIG`

```bash
export AMPLIFIER_AGENT_CONFIG=/etc/amplifier-agent/config.json
amplifier-agent config show
#   "host_config": {
#     "path": "/etc/amplifier-agent/config.json",
#     "source": "$AMPLIFIER_AGENT_CONFIG env",
#     ...
#   }
```

`--config` overrides this. To opt out without unsetting the var, pass `--config` with a different path (or use a temporary shell with `env -u AMPLIFIER_AGENT_CONFIG ...`).

### `AMPLIFIER_AGENT_WORKSPACE`

```bash
export AMPLIFIER_AGENT_WORKSPACE=my-project
amplifier-agent run "test" -y --session-id s1
ls ~/.amplifier-agent/state/workspaces/my-project/sessions/s1/
#   transcript.jsonl
#   metadata.json
#   audits/
#   context-intelligence/
```

Slug grammar: `[a-z0-9][a-z0-9-]{0,63}`. Leading `_` is reserved for internal workspaces (e.g. `_legacy` used by the auto-migration).

---

## Provider credentials

amplifier-agent never reads provider keys from the host config file — they come from the environment. The default provider is `anthropic`; to use a different provider, set the credential env var **and** add a `provider.module` entry to your host config.

| Provider | Primary env var | Legacy alias (still honored) |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | — |
| `openai` | `OPENAI_API_KEY` | — |
| `azure-openai` | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_KEY` (deprecated, one-time stderr warning) |
| `ollama` | `OLLAMA_HOST` | `OLLAMA_BASE_URL` (deprecated) |

If the configured provider's primary env var is unset, the run fails at mount time:

```
No API key found for Anthropic provider
Failed to load module 'provider-anthropic': ... No provider was mounted ...
```

Inside the TypeScript SDK, provider env vars are **not** in `DEFAULT_ALLOWLIST` — you must add them to your `env.allowlist` when calling `spawnAgent()`. See [TypeScript advanced: env allowlist](../typescript/advanced.md#environment-allowlist).

---

## Hook-side variables

These are read by bundle hooks, not by amplifier-agent itself. They are listed here so you know they exist.

| Variable | Read by | Purpose |
|---|---|---|
| `AMPLIFIER_MCP_CONFIG` | `tool-mcp` | Path to the MCP server config. Set by amplifier-agent when you provide `mcp.configPath` in the host config. You can also set it directly. |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | `hook-context-intelligence` | (Reserved.) If set, the hook would dispatch events to a remote server. Currently no server-config layer in amplifier-agent. |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | `hook-context-intelligence` | (Reserved.) Companion to the URL above. |

---

## Resolution order summary

For any setting that has both an env var and a flag, the precedence is **flag → env → bundle default → fail**:

| Setting | Flag | Env | Default |
|---|---|---|---|
| Host config path | `--config` | `AMPLIFIER_AGENT_CONFIG` | (none — no overlay) |
| Storage root | (none) | `AMPLIFIER_AGENT_HOME` | `~/.amplifier-agent` |
| Workspace | `--workspace` | `AMPLIFIER_AGENT_WORKSPACE` | (derived from cwd) |
| Approval mode | `-y` / `-n` | (none direct) | `host_config.approval.mode` → TTY check → fail |
| Provider | (none) | (none direct) | `host_config.provider.module` → `bundle.default_provider` |
