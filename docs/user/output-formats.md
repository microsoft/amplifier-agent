# Output formats

`amplifier-agent run` writes to two independent streams. `--output` controls stdout. `--display` controls stderr.

```
                          stdout                          stderr
--output text   →   "<reply>\n"
--output json   →   {<one-line JSON envelope>}\n

                                                    --display text    →  human-readable lines
                                                    --display ndjson  →  one wire event per line
```

`--output text --display ndjson` is a valid combination: stdout gets the plain reply, stderr gets the structured event stream.

---

## `--output text` (default)

The reply is printed to stdout followed by a newline. Nothing else goes to stdout. All progress, status, and diagnostic output is on stderr.

```
$ amplifier-agent run "Reply with only: ok" -y --session-id s1
ok
```

If something goes wrong before the model produces a reply, stdout is empty and stderr carries a `[error]` line:

```
$ amplifier-agent run -y -n "hello"
Error: -y and -n are mutually exclusive

$ amplifier-agent run "missing"          # no stdin, no -y/-n, no host config
[error] approval_unconfigured: ...       # appears on stdout in text mode
```

(One quirk: a few engine-level error paths emit the `[error]` to stdout because they happen before output routing is fully configured. Programs that script `amplifier-agent` should prefer `--output json` for unambiguous behavior.)

---

## `--output json`

A single-line JSON envelope is printed to stdout, regardless of success or failure. Use this in all scripts and host integrations.

### Success envelope

```json
{
  "protocolVersion": "0.3.0",
  "sessionId": "smoke-1",
  "turnId": "turn-1",
  "reply": "pong",
  "error": null,
  "metadata": {
    "tokensIn": 0,
    "tokensOut": 0,
    "durationMs": 21439,
    "bundleDigest": "",
    "engineVersion": "0.5.2",
    "protocolVersion": "0.3.0",
    "correlationId": "dd548f41-c9a4-4f08-b45b-203d9fc2b349"
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `protocolVersion` | string | Wire protocol version, currently `"0.3.0"`. |
| `sessionId` | string | The session ID. Empty string for anonymous runs. |
| `turnId` | string | Always `"turn-1"` in Mode A (each invocation is a fresh subprocess). |
| `reply` | string | The model's text reply. |
| `error` | `null` or error object | See below. |
| `metadata.tokensIn` / `tokensOut` | int | Currently `0` in the envelope; live usage is reported via NDJSON wire events instead. |
| `metadata.durationMs` | int | Engine-side wall-clock duration. |
| `metadata.bundleDigest` | string | Reserved; currently empty. |
| `metadata.engineVersion` | string | The amplifier-agent version. |
| `metadata.protocolVersion` | string | Mirrors top-level for convenience. |
| `metadata.correlationId` | uuid string | Unique per `run` invocation. Appears in audit records and wire events. |

### Error envelope

```json
{
  "protocolVersion": "0.3.0",
  "sessionId": "",
  "turnId": "",
  "reply": "",
  "error": {
    "code": "approval_unconfigured",
    "classification": "protocol",
    "severity": "error",
    "correlationId": "d473ad6f-c72e-46f3-8e4a-593babf513b8",
    "message": "Headless run requires an explicit approval policy. ...",
    "remediation": "Pass `-y` to auto-approve, `-n` to auto-deny, or set ..."
  },
  "metadata": { ... }
}
```

Error object fields:

| Field | Type | Description |
|---|---|---|
| `code` | string | Stable identifier. See [Error codes](#error-codes-reference). |
| `classification` | `engine` \| `protocol` \| `approval` \| `transport` \| `unknown` | Drives exit code mapping. |
| `severity` | `error` (current) | Reserved for future warnings. |
| `correlationId` | uuid | Same as the top-level envelope `correlationId`. |
| `message` | string | Human-readable description. |
| `remediation` | string (optional) | How to fix the error. |
| `stderrTail` | string (optional) | Last N bytes of subprocess stderr (set by the TypeScript SDK; not by the engine itself). |

---

## Exit codes and error classifications

| Exit code | Classification | When it happens |
|---|---|---|
| `0` | — | Successful run. |
| `1` | `engine`, `transport`, `unknown` | Provider failure, kernel error, internal bug. |
| `2` | `protocol` | Bad argv, host-config parse error, protocol version mismatch, missing prompt, approval unconfigured. |
| `3` | `approval` | The model requested a tool that approval denied during the run. |

The mapping is fixed in the engine (`_EXIT_CODE_BY_CLASSIFICATION`). The TypeScript SDK applies the same mapping when synthesizing error envelopes from a subprocess that crashed before producing a JSON envelope.

---

## `--display text` (default)

Stderr emits human-readable summaries:

```
[usage] in=4202 out=99 cost=$0.0122376 cache_read=4192 cache_write=2524 dur=3540ms model=claude-sonnet-4-5 provider=anthropic
[result/final]
[result/delta] pong
[usage] in=0 out=0 session_total=$0.0122376
```

`[type]` prefixes correspond to the canonical wire events. Verbosity tiers (in increasing order):

| Tier | Flag | What stderr shows |
|---|---|---|
| `quiet` | `--quiet` | Nothing. |
| `normal` | (default) | Brief summaries. |
| `verbose` | `-v` / `--verbose` | Adds tool call argument summaries, agent transitions. |
| `debug` | `--debug` | Adds kernel events, internal state. |

`--quiet` is mutually exclusive with `-v` and `--debug`.

---

## `--display ndjson`

Stderr emits one JSON object per line, one per wire event. This is what the TypeScript SDK consumes.

```
{"method": "usage", "params": {"sessionId": "", "turnId": "turn-1", "inputTokens": 4202, "outputTokens": 115, "llmDurationMs": 5439, "model": "claude-sonnet-4-5", "provider": "anthropic", "cacheReadTokens": 4192, "cacheWriteTokens": 2540, "cost": "0.0125376"}}
{"method": "result/final", "params": {"sessionId": "", "turnId": "turn-1", "text": ""}}
{"method": "result/delta", "params": {"sessionId": "", "turnId": "turn-1", "text": "echo only"}}
{"method": "usage", "params": {"sessionId": "", "turnId": "turn-1", "inputTokens": 0, "outputTokens": 0, "sessionCostTotal": "0.0125376"}}
```

Each line has a `method` and a `params` object. The verbosity flags (`-v`, `--debug`, `--quiet`) **do not affect** ndjson output — the full event stream is always emitted.

### Canonical wire event types

| Method | Params (typical) | When emitted |
|---|---|---|
| `result/delta` | `{sessionId, turnId, text}` | A chunk of the model's textual reply. May fire many times. |
| `result/final` | `{sessionId, turnId, text}` | End-of-reply marker. `text` is typically empty (the deltas already carried it). |
| `tool/started` | `{sessionId, turnId, tool, ...}` | The model called a tool. |
| `tool/completed` | `{sessionId, turnId, tool, result, ...}` | A tool call returned. |
| `thinking/delta` | `{sessionId, turnId, text}` | Extended-thinking chunk (when the model uses thinking). |
| `thinking/final` | `{sessionId, turnId, text}` | End-of-thinking marker. |
| `usage` | `{inputTokens, outputTokens, cost, model, provider, ...}` | Token accounting. Emitted per LLM call and at session end (`sessionCostTotal`). |

> **Note on the shape:** events are NDJSON, not full JSON-RPC. They have `method` and `params` but not the `"jsonrpc": "2.0"` field. The TypeScript SDK parses them as a stream of notification-like objects.

---

## Combining `--output` and `--display`

```bash
# Human-friendly default — text reply, text diagnostics.
amplifier-agent run "..." -y

# Scripted — JSON envelope, text diagnostics.
amplifier-agent run "..." -y --output json > out.json

# Quiet — JSON envelope only, nothing on stderr.
amplifier-agent run "..." -y --output json --quiet > out.json

# Wrapper — JSON envelope on stdout, structured events on stderr.
amplifier-agent run "..." -y --output json --display ndjson \
    > out.json 2> events.ndjson
```

The TypeScript SDK uses the last form (`--output json --display ndjson`) under the hood.

---

## Error codes reference

This is not exhaustive — codes evolve. Surface unknowns by reading the `message` field.

| Code | Classification | Exit | Where |
|---|---|---|---|
| `approval_unconfigured` | `protocol` | 2 | Headless run with no `-y`/`-n` and no `host_config.approval.mode`. |
| `protocol_version_mismatch` | `protocol` | 2 | `--protocol-version` does not equal the engine's compiled version, and `allowProtocolSkew` is false. |
| `config_unreadable` | `protocol` | 2 | The host config file can't be read. |
| `config_unknown_key` | `protocol` | 2 | Top-level key outside the closed set. |
| `config_invalid_type` | `protocol` | 2 | Closed-inner-shape violation inside `skills:` block. Other blocks (`provider.config`, `approval`, `mcp`) are pass-through. |
| `config_invalid_provider_module` | `protocol` | 2 | `provider.module` is not a known provider. |
| `config_no_matching_module` | `protocol` | 2 | Host declares `skills:` but bundle has no `tool-skills` mount. |
| `prompt_required` | `protocol` | 2 | No prompt passed and stdin is not a TTY. |
| `env_injection_rejected` | `protocol` | 2 | (TypeScript SDK) Caller tried to pass a blocked env key (e.g. `PYTHONPATH`). |

Approval-class codes (exit 3) arise during a run when the model attempts a tool call that approval denies; they share `classification: "approval"`.
