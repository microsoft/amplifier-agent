# CLI reference

Complete reference for every subcommand and every flag.

```
amplifier-agent [--version] COMMAND [ARGS]...

Commands:
  cache    Manage the prepared-bundle cache.
  config   Inspect resolved config.
  doctor   Run self-diagnostics and report system health.
  models   Enumerate models available from a provider.
  prepare  Prime the bundle cache (install-time warm-up).
  run      Run the agent in single-turn mode (Mode A).
  update   Check for and install the latest amplifier-agent release.
  verify   Verify the installation and hook coverage.
  version  Show engine version and wire protocol version.
```

## Global

| Flag | Description |
|---|---|
| `--version` | Print version and exit (`amplifier-agent, version 0.5.2`). |
| `--help` | Show help for any command. |

---

## `run`

Run a single turn: submit one prompt, receive one reply.

```
amplifier-agent run [OPTIONS] PROMPT
```

`PROMPT` is required as a positional argument. Stdin is **not** read for the prompt — passing it via pipe will fail with `[error] prompt_required`. (The `is_stdin_tty()` check is used for the *approval-mode* fallback, not prompt input.)

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--session-id TEXT` | string | (anonymous) | Session ID to resume or tag. Omit for an anonymous run that writes no audit record. |
| `--resume` | flag | off | Resume an existing session: replay transcript before this prompt. Mutex with `--fresh`. |
| `--fresh` | flag | off | Force a fresh session — delete any saved state for this session ID before starting. Mutex with `--resume`. |
| `--config PATH` | path | — | Path to a host config JSON file. Overrides `$AMPLIFIER_AGENT_CONFIG`. |
| `--cwd PATH` | path | (process cwd) | Working directory the agent sees and uses for relative file paths. |
| `-v, --verbose` | flag | off | Verbose stderr output. Only meaningful with `--display text`; ignored under `--display ndjson`. |
| `--debug` | flag | off | Maximum stderr verbosity. Only meaningful with `--display text`. |
| `--quiet` | flag | off | Suppress all stderr diagnostics. Mutex with `-v`/`--debug`. |
| `-y, --yes` | flag | off | Auto-approve every tool call. Mutex with `-n`. |
| `-n, --no` | flag | off | Auto-deny every tool call. Mutex with `-y`. |
| `--output [text|json]` | enum | `text` | Stdout format. `text` prints `reply + "\n"`. `json` prints a single-line envelope. |
| `--display [text|ndjson]` | enum | `text` | Stderr format. `text` is human-readable. `ndjson` emits one JSON-RPC-shaped event per line. |
| `--protocol-version TEXT` | string | — | Wrapper's pinned protocol version. Engine fails with `protocol_version_mismatch` if it doesn't match (currently `0.3.0`), unless `allowProtocolSkew` is set. |
| `--workspace TEXT` | string | (auto from cwd) | Workspace slug. Isolates session state by project. Falls back to `$AMPLIFIER_AGENT_WORKSPACE`, then a deterministic slug derived from `--cwd`. |

### Validation

The following combinations fail at argv parse time with exit code `2`:

```
$ amplifier-agent run "x" -y -n
Error: -y and -n are mutually exclusive

$ amplifier-agent run "x" --resume --fresh
Error: --resume and --fresh are mutually exclusive

$ amplifier-agent run "x" -v --quiet
Error: --quiet conflicts with -v/--verbose and --debug; choose one verbosity tier
```

Headless runs without an explicit approval policy fail fast:

```
$ amplifier-agent run "x" --output json < /dev/null
{"protocolVersion":"0.3.0","sessionId":"","turnId":"","reply":"","error":{
  "code":"approval_unconfigured","classification":"protocol","severity":"error",
  "message":"Headless run requires an explicit approval policy. Stdin is not a
  TTY, neither -y/--yes nor -n/--no was passed, and host_config does not set
  `approval.mode`. ...",
  "remediation":"Pass `-y` to auto-approve, `-n` to auto-deny, or set
  `{\"approval\": {\"mode\": \"yes\"|\"no\"|\"prompt\"}}` in your --config /
  $AMPLIFIER_AGENT_CONFIG file."}, ...}
# exit code 2
```

### Examples

```bash
# Simplest run — text reply on stdout.
amplifier-agent run "Hello" --session-id s1 -y

# JSON envelope for scripting.
amplifier-agent run "Hello" --session-id s1 -y --output json

# Continue a previous session.
amplifier-agent run "Now what?" --session-id s1 --resume -y

# Wipe and start over for the same session ID.
amplifier-agent run "Fresh start" --session-id s1 --fresh -y

# Structured event stream on stderr (for hosts).
amplifier-agent run "Hello" --session-id s1 -y \
  --output json --display ndjson 2>events.jsonl
```

See [Output formats](output-formats.md) for the JSON envelope and NDJSON event shapes.

---

## `config`

Inspect host configuration.

### `config show`

Print the resolved configuration as JSON with source annotations.

```
amplifier-agent config show [--config PATH]
```

| Flag | Type | Description |
|---|---|---|
| `--config PATH` | path | Show the configuration that would result from this file. Overrides `$AMPLIFIER_AGENT_CONFIG`. |

The output reports four blocks:

```json
{
  "provider": {
    "value": "anthropic",
    "source": "bundle.default_provider"
  },
  "host_config": {
    "path": "/path/to/cfg.json",
    "source": "--config flag",
    "parsed": { ... }
  },
  "skills": {
    "skills": [ ... bundle defaults + host appended ... ],
    "visibility": { ... bundle defaults overlaid by host ... }
  },
  "amplifier_agent_home": {
    "value": "/root/.amplifier-agent",
    "source": "default"
  }
}
```

Source annotations:

| Block | Possible sources |
|---|---|
| `provider.source` | `"bundle.default_provider"`, `"host_config.provider.module"` |
| `host_config.source` | `"--config flag"`, `"$AMPLIFIER_AGENT_CONFIG env"`, `"none"` |
| `amplifier_agent_home.source` | `"env:AMPLIFIER_AGENT_HOME"`, `"default"` |

On parse error, `parsed` is `null` and `parse_error` reports the code and message — `config_unknown_key`, `config_invalid_provider_module`, `config_unreadable`, `config_invalid_type`, etc. See [Configuration](configuration.md).

---

## `doctor`

Run self-diagnostics. Use it whenever something seems wrong.

```
amplifier-agent doctor [--strict] [--quick] [--emit-sha]
```

| Flag | Description |
|---|---|
| `--strict` | Exit non-zero on warnings (for CI / image-build gating). Without `--strict`, a missing prepared cache is `[INFO]` only. |
| `--quick` | Run minimal checks: Python version and prepared-cache presence. Skips bundle, MCP, XDG writability, and contract checks. |
| `--emit-sha` | Append a line per bundle module with `sha256_prefix=...  module=...  source=...`. Currently the SHA is of the source URL string (v1 stub); full content SHA is a future enhancement. |

### Checks (full mode)

1. Python version (`>= 3.11`)
2. Bundle declares a `default_provider` string
3. `config` root is writable
4. `cache` root is writable
5. `state` root is writable
6. Bundle modules invariants: `context-simple` mounted, `tool-mcp` mounted, `hooks-logging` *not* mounted
7. `WireApprovalProvider` subclass + all three error codes present
8. `SessionStore` write/read roundtrip in a tempdir succeeds
9. `mcp` module is importable
10. Prepared bundle cache presence (`[INFO]` by default; `[FAIL]` with `--strict`)

Failure of checks 1–9 → exit code 1. With `--strict`, the cache check also gates exit code.

### Sample output

```
$ amplifier-agent doctor
[ OK ] python: 3.12.3
[ OK ] bundle default_provider: anthropic
[ OK ] config home: /root/.amplifier-agent/config
[ OK ] cache home: /root/.amplifier-agent/cache
[ OK ] state home: /root/.amplifier-agent/state
[ OK ] bundle modules: context-simple, tool-mcp present; hooks-logging absent
[ OK ] wire_approval_provider: subclass check passed; all three error codes present
[ OK ] session_store: write/read roundtrip in tempdir succeeded
[ OK ] mcp module: importable
[INFO] bundle cache: needs prepare (/root/.amplifier-agent/cache/prepared/0.5.2/da41ba6300040dd9)
```

---

## `cache`

Manage the prepared-bundle cache at `~/.amplifier-agent/cache/prepared/`.

### `cache clear`

Remove every prepared bundle (all versions, all SHAs). Idempotent — succeeds even if nothing was cached.

```
amplifier-agent cache clear
```

No flags. The next `run` (or explicit `prepare`) will re-clone and re-install the bundle's modules.

---

## `models`

Enumerate models from one or more providers.

### `models list`

```
amplifier-agent models list [OPTIONS]
```

Two modes:

- **Single-provider** with `--provider <name>` — query one provider and emit the single-provider envelope.
- **Aggregate** (no `--provider`) — query every known provider in parallel and emit a per-provider results envelope.

| Flag | Type | Default | Description |
|---|---|---|---|
| `--provider TEXT` | string | (aggregate) | One of `anthropic`, `openai`, `azure-openai`, `ollama`. Omit for aggregate mode. |
| `--output [auto|json|table]` | enum | `auto` | `auto` → table on TTY, JSON otherwise. |
| `--timeout FLOAT` | number | `15.0` | Request timeout in seconds. |
| `--latest` | flag | off | Return only the latest model per family (provider-default filtering). |

### Single-provider envelope

```json
{
  "schema_version": 1,
  "provider": "anthropic",
  "fetched_at": "2026-06-12T08:22:47.890330+00:00",
  "models": [
    {
      "id": "claude-haiku-4-5-20251001",
      "display_name": "Claude Haiku 4.5",
      "context_window": 200000,
      "max_output_tokens": 64000,
      "capabilities": ["tools", "streaming", "json_mode", "fast", "vision", "thinking"],
      "defaults": { "temperature": 0.7, "max_tokens": 64000 }
    },
    ...
  ]
}
```

### Aggregate envelope

```json
{
  "schema_version": 1,
  "fetched_at": "2026-06-12T08:23:46.323889+00:00",
  "results": [
    { "provider": "anthropic", "status": "ok", "models": [ ... ] },
    { "provider": "openai", "status": "credentials_missing" },
    { "provider": "azure-openai", "status": "module_not_installed" },
    { "provider": "ollama", "status": "error", "error": "..." }
  ]
}
```

`status` values: `ok`, `credentials_missing`, `module_not_installed`, `error`.

Providers without credentials emit a one-line stderr notice and return a `credentials_missing` status — they don't error the whole call:

```
$ amplifier-agent models list --provider openai
# openai: OPENAI_API_KEY not set; cannot fetch live model list. Set the env var or choose a different provider.
```

---

## `prepare`

Pre-warm the bundle cache. Use in CI images, container builds, or before the first end-user run.

```
amplifier-agent prepare
```

No flags. Equivalent to running the post-install hook (`amplifier-agent-post-install`) but exits non-zero on failure. The post-install hook swallows errors so a failed prep doesn't break installation.

What it does:

1. Read the vendored `bundle.md`.
2. Resolve modules via the foundation source resolver — `git clone` then `pip install` each module into the cache.
3. Mount-time validation (`mount()` for every module).
4. Pickle the prepared bundle to `~/.amplifier-agent/cache/prepared/<version>/<sha[:16]>/prepared.pickle`.
5. Write a sibling `manifest.json` for cache-key inspection.

---

## `verify`

Verify installation invariants.

```
amplifier-agent verify [--check-hooks]
```

| Flag | Description |
|---|---|
| `--check-hooks` | Verify that the canonical wire-event set is exposed by the streaming hook. |

Default mode is a no-op (`[ OK ] verify: nothing to check`). The interesting use is `--check-hooks`, which asserts the engine's `CANONICAL_WIRE_EVENTS` covers the minimum: `result/delta`, `result/final`, `tool/started`, `tool/completed`, `usage`.

```
$ amplifier-agent verify --check-hooks
[ OK ] hook coverage passes — all minimum-set events present
```

---

## `version`

```
amplifier-agent version [--json]
```

| Flag | Description |
|---|---|
| `--json` | Emit a one-line JSON payload. |

### Output

```
$ amplifier-agent version
amplifier-agent 0.5.2 (wire 0.3.0)

$ amplifier-agent version --json
{"version": "0.5.2", "protocolVersion": "0.3.0"}
```

The TypeScript SDK's `probeEngineVersion()` calls this with `--json`.

---

## `update`

```
amplifier-agent update [--check] [--tag REF] [--force] [--output {text|json}]
```

| Flag | Description |
|---|---|
| `--check` | Show status only; do not install. |
| `--tag TEXT` | Install a specific tag, branch, or SHA. |
| `--force` | Reinstall even when versions match. |
| `--output [text|json]` | Output format. |

Wraps `uv tool install --reinstall --force git+...@<tag>` with install-method detection so editable dev checkouts are not clobbered.

```
$ amplifier-agent update --check
Checking latest amplifier-agent release...
  Current:  0.5.2
  Latest:   0.5.2  (v0.5.2 from 2026-06-09T04:02:00Z)
  Install:  editable
```

`Install:` values: `uv-tool` (the normal case), `editable`, `other`.

After a successful install, `update` runs the legacy XDG-to-`~/.amplifier-agent/` migration once. See [Installation: Updating](installation.md#updating).

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Engine, transport, or unknown error during a `run`. |
| `2` | Protocol error (argv validation, host-config parse, protocol version mismatch, approval unconfigured, missing prompt). |
| `3` | Approval error during a `run` (the model wanted a tool that approval denied). |

See [Output formats: exit codes](output-formats.md#exit-codes-and-error-classifications) for the full error envelope schema.
