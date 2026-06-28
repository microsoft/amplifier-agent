# Configuration

amplifier-agent ships a sealed bundle with hard-coded defaults. To override any of them, you provide a **host config file** — a JSON document that parameterizes specific blocks the bundle exposes.

## Where the config comes from

amplifier-agent resolves the host config in this order (first match wins):

1. `--config <path>` flag.
2. `$AMPLIFIER_AGENT_CONFIG` env var.
3. Neither set → no host config; bundle defaults stand.

If `$AMPLIFIER_AGENT_CONFIG` is set to a path that doesn't exist, the run fails with `config_unreadable` — it is not silently skipped.

To see what config will actually be used:

```bash
amplifier-agent config show --config /path/to/cfg.json
```

## The schema

The config file is JSON. The top level is **closed**: only these five keys are accepted. Any other key produces `config_unknown_key`:

```json
{
  "provider":          { ... },
  "approval":          { ... },
  "skills":            { ... },
  "mcp":               { ... },
  "allowProtocolSkew": false
}
```

The schema is intentionally minimal. amplifier-agent only lets you parameterize what `bundle.md` already declares — there is no schema translation, no key renaming, no recursive merging. If the bundle exposes a key, the host can set it; otherwise the bundle default stands.

---

### `provider`

Select which LLM provider mounts and pass per-provider parameters.

```json
{
  "provider": {
    "module": "anthropic",
    "config": {
      "default_model": "claude-sonnet-4-5",
      "temperature": 0.7,
      "max_tokens": 8000,
      "thinking_budget_tokens": 1024,
      "effort": "medium"
    }
  }
}
```

| Key | Type | Description |
|---|---|---|
| `provider.module` | string | One of `anthropic`, `openai`, `azure-openai`, `ollama`. Anything else → `config_invalid_provider_module`. |
| `provider.config` | object | Pass-through to the provider's mount config. The merger overlays this on top of the bundle's provider defaults per key. |

The `provider.config` block is **pass-through**. amplifier-agent does not validate the inner keys — they reach the provider's `mount()` unchanged. The keys recognized by current providers include `default_model`, `effort`, `temperature`, `max_tokens`, `thinking_budget_tokens`. Future provider-specific keys flow through automatically.

The API key for each provider comes from the **environment**, not from this file:

| Provider | Env var | Legacy alias |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | — |
| `openai` | `OPENAI_API_KEY` | — |
| `azure-openai` | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_KEY` (deprecated, warned once) |
| `ollama` | `OLLAMA_HOST` | `OLLAMA_BASE_URL` (deprecated) |

If the named provider's credential env var is unset, the run fails at mount time with a clear stderr message.

---

### `approval`

Control how tool calls are approved.

```json
{
  "approval": {
    "mode": "yes",
    "patterns": []
  }
}
```

| Key | Type | Description |
|---|---|---|
| `approval.mode` | `"yes"` \| `"no"` \| `"prompt"` | Headless default. |
| `approval.patterns` | array of strings | Glob patterns for tool-name routing (pass-through to `hooks-approval`). |

`approval.mode` resolution order (the engine picks the **first defined** value):

1. `-y` argv flag → `"yes"`.
2. `-n` argv flag → `"no"`.
3. `host_config.approval.mode`.
4. Stdin is a TTY → `"prompt"`.
5. Non-TTY, no explicit policy → **fail fast** with `approval_unconfigured`.

This means a headless run (CI, container, piped invocation) that does *not* set `-y`, `-n`, or `approval.mode` will refuse to start. Earlier versions silently auto-denied, producing success-shaped no-op runs; the fail-fast was added to prevent that footgun.

---

### `skills`

Extend the skills the agent can load and tune how skills appear in context.

```json
{
  "skills": {
    "skills": ["~/my-skills", "git+https://github.com/me/extra-skills"],
    "visibility": {
      "enabled": true,
      "inject_role": "user",
      "max_skills_visible": 25,
      "ephemeral": true,
      "priority": 20
    }
  }
}
```

Two sub-keys with **different merge semantics**:

| Sub-key | Shape | Merge rule |
|---|---|---|
| `skills.skills` | list of strings | **List-concat**: bundle defaults first, host entries appended. The bundle is the floor; the host can only *extend*, never *strip*. |
| `skills.visibility` | object | **Shallow per-key overlay**: bundle keys come through unless the host overrides them per key. |

So if the bundle ships with three default skill sources and you add one, the resulting list has four entries — verified with `amplifier-agent config show --config ...`:

```json
"skills": {
  "skills": [
    "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
    ".amplifier/skills",
    "~/.amplifier/skills",
    "~/my-skills"
  ],
  ...
}
```

If you declare `skills` in your config but the bundle doesn't have a `tool-skills` mount, the merger refuses with `config_no_matching_module` — it won't silently fabricate a config for a module that won't be mounted.

---

### `mcp`

Point the agent at an [MCP](https://modelcontextprotocol.io/) server configuration file.

```json
{
  "mcp": {
    "configPath": "/path/to/mcp-servers.json"
  }
}
```

| Key | Type | Description |
|---|---|---|
| `mcp.configPath` | string | Path to an MCP server config file (the format `tool-mcp` expects). Translated to `AMPLIFIER_MCP_CONFIG` env var. |

The host config layer is the only way to point `tool-mcp` at a config file from amplifier-agent (the former `--mcp-config-path` argv flag was removed; the host config is the single source of truth).

---

### `allowProtocolSkew`

```json
{
  "allowProtocolSkew": true
}
```

When `true`, the engine boot bypasses the protocol-version-mismatch check. Useful in dev when the TypeScript SDK and the Python engine are out of sync. **Unsafe in production** — version mismatch means the SDK and engine may disagree on wire-event shapes.

Default: `false`.

---

## Examples

### Use OpenAI with GPT-4o

```json
{
  "provider": {
    "module": "openai",
    "config": { "default_model": "gpt-4o" }
  },
  "approval": { "mode": "yes" }
}
```

### Run against a local Ollama daemon

```bash
export OLLAMA_HOST=http://localhost:11434
```

```json
{
  "provider": {
    "module": "ollama",
    "config": { "default_model": "llama3.1:70b" }
  },
  "approval": { "mode": "yes" }
}
```

### Headless CI run with Anthropic

```json
{
  "approval": { "mode": "yes" },
  "provider": {
    "module": "anthropic",
    "config": {
      "default_model": "claude-haiku-4-5",
      "max_tokens": 4000
    }
  }
}
```

### Add custom skills to the default set

```json
{
  "skills": {
    "skills": [
      "git+https://github.com/myorg/my-skills@main",
      "./.team-skills"
    ]
  }
}
```

The four bundle-default skill sources are preserved; the two extras are appended.

---

## Validation errors

All host config parse errors share a uniform `{code, message}` shape. Surface them via:

```bash
amplifier-agent config show --config /path/to/cfg.json
```

| Code | When |
|---|---|
| `config_unreadable` | File not found or unreadable. |
| `config_unknown_key` | Top-level key outside the closed set `{provider, approval, skills, mcp, allowProtocolSkew}`. |
| `config_invalid_type` | Closed-inner-shape violation (currently: a key inside `skills.*` other than `skills` or `visibility`, or `skills`/`skills.visibility` having a non-list/non-dict shape). The other blocks (`provider.config`, `approval`, `mcp`) are pass-through and silently ignored if not a dict. |
| `config_invalid_provider_module` | `provider.module` not in `{anthropic, openai, azure-openai, ollama}`. |
| `config_no_matching_module` | Host declares `skills:` but bundle has no `tool-skills` mount. |

On parse failure during a `run`, the envelope's `error` field carries the same code with `classification: "protocol"` and the run exits `2`.
