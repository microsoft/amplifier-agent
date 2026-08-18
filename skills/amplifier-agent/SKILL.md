---
name: amplifier-agent
description: >-
  Build software on amplifier-agent, the Microsoft agent engine that other
  software runs on. Use when (1) adding an AI agent, agent loop, or chat backend
  to an app, service, CLI, or bot, (2) integrating amplifier-agent from
  TypeScript/Node, Python, HTTP, or a shell script, (3) choosing between the
  TypeScript SDK, Python SDK, in-process library, HTTP face, and raw CLI
  contract, (4) debugging a host that spawns the engine: approval_unconfigured,
  protocol_version_mismatch, binary_not_found, lost session continuity, or
  stdout/stderr parsing. Triggers on "amplifier-agent", "amplifier agent",
  "amplifier-agent-ts", "amplifier_agent_py", "spawnAgent", "spawn_agent_sync",
  "serve chat-completions", "amplifier_agent_lib", "add an agent to my app".
license: MIT
metadata:
  author: microsoft
  version: "0.1.0"
  repository: https://github.com/microsoft/amplifier-agent
---

# Integrating amplifier-agent

`amplifier-agent` is an agent engine that other software runs on. Give it a prompt and it runs the full loop, with tools, sub-agents, skills, and MCP, then returns a result. Anything that can spawn a subprocess can use it; Python hosts can embed the engine library in-process instead.

Reach for it when the project needs an *agent* (a tool loop, file access, sub-agents, multi-turn state) rather than a single completion. You can also use it for plain LLM calls, with routing across six providers behind one interface.

**The engine runs one turn per invocation and exits.** Continuity across turns comes from a session id, not from a long-lived process. Every surface below is a different way of delivering a prompt to that same engine.

## Before writing code

Confirm every flag, field, and option name against the docs below or a local install before you write it. Do not fill gaps from memory.

| Need | Source |
|---|---|
| Integration surfaces | <https://github.com/microsoft/amplifier-agent/blob/main/docs/INTEGRATION.md> |
| Install, pin, update | <https://github.com/microsoft/amplifier-agent/blob/main/docs/INSTALL.md> |
| Providers, credentials, host config | <https://github.com/microsoft/amplifier-agent/blob/main/docs/CONFIGURATION.md> |
| Every command and flag | <https://github.com/microsoft/amplifier-agent/blob/main/docs/CLI.md> |
| Normative contracts (wire protocol, envelope, host config, HTTP face) | <https://github.com/microsoft/amplifier-agent/blob/main/docs/SPEC.md> |
| Who already integrated, and how | <https://github.com/microsoft/amplifier-agent/blob/main/docs/ECOSYSTEM.md> |

Against a local install, the binary is authoritative: `amplifier-agent version` prints the engine and wire protocol versions, `amplifier-agent doctor` reports env, providers, paths, and bundle cache, and `--help` on any command prints the real flags. If you cannot confirm something from the docs, the binary, or the SDK type definitions, say so rather than guessing.

## Install the engine first

Every surface needs the engine. The SDKs are **BYO-engine**: they have zero runtime dependencies and locate the `amplifier-agent` binary on `PATH` (or at `AMPLIFIER_AGENT_BIN`).

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash
amplifier-agent doctor
```

The installer needs `uv` and `curl` and will not bootstrap them silently; it tells you which is missing and stops. If `uv` is absent, install it first with `curl -LsSf https://astral.sh/uv/install.sh | sh`, then re-run. Pin a release by appending `-s -- --tag v0.12.0`, and add `--yes` in CI or a Dockerfile to skip the prompt.

Install as **the same user that runs the host process**; a host spawning a subprocess inherits that user's `PATH`. `amplifier-agent doctor` is the check that the install actually works, so run it before writing any integration code.

Credentials are read from the environment, first match wins: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY` plus `AZURE_OPENAI_ENDPOINT`, `OLLAMA_HOST`. GitHub Copilot is environment-only (`COPILOT_AGENT_TOKEN`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`). ChatGPT (`openai-chatgpt`) has no credential env var at all: it authenticates via OAuth device-code, caching tokens to `~/.amplifier/openai-chatgpt-oauth.json`. Or store a static key with `amplifier-agent auth set anthropic sk-ant-...` (not supported for github-copilot or openai-chatgpt).

## Pick a surface

| You are writing | Use |
|---|---|
| Node.js or TypeScript | `amplifier-agent-ts` npm package |
| Python, separate process | `amplifier-agent-py` wrapper |
| Python, same process | `amplifier_agent_lib` directly |
| Anything that speaks HTTP | `amplifier-agent serve chat-completions` |
| A shell script, or a language with no SDK | The CLI contract |

## TypeScript SDK

```bash
npm install amplifier-agent-ts     # Node.js 20+, zero runtime dependencies
```

```typescript
import { spawnAgent } from 'amplifier-agent-ts';
import { randomUUID } from 'node:crypto';

const session = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: randomUUID(),
});

for await (const event of session.submit('Hello, agent.')) {
  if (event.type === 'result') {
    console.log(event.text);
  } else if (event.type === 'error') {
    console.error(`[${event.code}] ${event.message}`);
  }
}
```

`submit()` returns an async iterable of `DisplayEvent` (`init`, `activity`, `result`, `error`, `notification`) and is **one-shot per session**; a second call throws `AaaError('lifecycle_unsupported')`. Spawn a new handle for the next turn, reusing the same `sessionId` with `resume: true` for continuity.

`spawnAgent` options: `lifecycle` (must be `'one-shot'`), `sessionId`, `resume`, `workspace`, `cwd`, `approval` (`{ mode: 'yes' | 'no' | 'prompt' }`), `configPath`, `env` (`{ allowlist, extra }`), `mcpServers`, `displayMode`, `display`, `timeoutMs`, `allowProtocolSkew`. Type definitions ship at `node_modules/amplifier-agent-ts/dist/index.d.ts`; read them rather than recalling the shape.

> The `submit()` example in [`wrappers/typescript/README.md`](https://github.com/microsoft/amplifier-agent/blob/main/wrappers/typescript/README.md) is stale (it awaits a `{ reply }` object). Follow the iterable form above, which matches the shipped types.

## Python SDK

Use this when you want process isolation between host and engine.

```bash
# Not on PyPI yet; install from the git source
uv add "amplifier-agent-py @ git+https://github.com/microsoft/amplifier-agent#subdirectory=wrappers/python-py"
```

```python
from amplifier_agent_py import spawn_agent_sync

with spawn_agent_sync(
    session_id="chat-42",
    workspace="my-app",
    display_mode="ndjson",
    approval={"mode": "yes"},
    timeout_ms=300_000,
) as handle:
    for event in handle.submit("Hello, agent."):
        if event.type == "result":
            print(event.text)
        elif event.type == "error":
            print(f"[{event.code}] {event.message}")
```

`spawn_agent` is the async variant, returning a handle whose `submit()` is an async iterator; call `await handle.dispose()` when done. Event fields are snake_case here (`session_id`, `correlation_id`, `stderr_tail`). Parameters mirror the TypeScript SDK one for one, and that symmetry is enforced by a conformance suite.

## In-process library

`amplifier_agent_lib` is transport-free Python, and the CLI binary is a thin I/O adapter over it, so both paths share all engine behavior. You give up process isolation. The public contract is the `Engine` class: `boot()`, then `submit_turn()` per turn, then `shutdown()`. Read [`docs/spec/engine-api.md`](https://github.com/microsoft/amplifier-agent/blob/main/docs/spec/engine-api.md) before using it; it is the normative contract and names every public symbol.

## HTTP face

```bash
amplifier-agent serve chat-completions
```

An OpenAI-compatible, bearer-authenticated surface, for hosts that already speak the OpenAI chat completions protocol. It also exposes `GET /v1/skills` and `GET /v1/modes`. Select a mode with an `[amplifier-agent:mode=<name>]` directive in a `system` or `developer` message; the resolved mode returns as a top-level `activeMode` on every response.

> **The approval policy is inert on this path. Every tool call is auto-approved.** The chat-completions wire has no human-in-the-loop channel, so a host relying on `approval.mode` for safety gets no protection here. Isolate the server accordingly.

## Shell, or any language without an SDK

```bash
amplifier-agent run -y --session-id chat-42 --workspace my-app \
  --output json --display ndjson "Hello, agent."
```

Stdout carries a single JSON envelope (`protocolVersion`, `sessionId`, `turnId`, `reply`, `error`, `metadata`). Under the default `--output text` it is the reply text only. Diagnostics (tool calls, progress) go to **stderr** only, as one JSON-RPC notification per line under `--display ndjson`.

Continuity is per `(workspace, session-id)`:

```bash
amplifier-agent run -y --session-id chat-42 "My favorite color is blue."
amplifier-agent run -y --session-id chat-42 --resume "What is my favorite color?"
amplifier-agent run -y --session-id chat-42 --fresh "Start over."
```

`--resume` and `--fresh` are mutually exclusive.

## Rules that break integrations when ignored

1. **Declare an approval policy explicitly.** A headless host must pass `-y`, `-n`, or `approval.mode` in a host config. With none of those and no TTY, the run exits 2 with `approval_unconfigured` rather than silently doing nothing and exiting 0.
2. **Set `--workspace`.** Without it, sessions are scoped to the current working directory, so a host that spawns from varying directories sees its sessions fragment. Multi-tenant hosts always set it.
3. **Pin the protocol version and fail loudly.** Wrappers pass `--protocol-version`; a mismatch returns `protocol_version_mismatch` and exits non-zero instead of misbehaving quietly.
4. **Never parse stderr for results.** Streams are strictly separated. Parse stdout for the envelope, stderr for progress.
5. **One host config file per agent instance.** Pass `--config <path>` every turn. The top level is closed (`approval`, `provider`, `providers`, `mcp`, `skills`, `debug`, `allowProtocolSkew`); an unknown key is an error, not a warning.
6. **Record `metadata.engineVersion` and `metadata.bundleDigest`** from the envelope alongside your own logs, so a behavior change is attributable.
7. **Install the engine as the user that runs the host**, and verify with `amplifier-agent doctor` at deploy time.

A per-instance config file looks like this:

```json
{
  "approval": { "mode": "yes" },
  "provider": { "module": "anthropic", "config": { "default_model": "claude-sonnet-5" } },
  "mcp": { "configPath": "/var/run/myapp/instances/<agent-id>/mcp.json" },
  "skills": { "skills": ["/var/run/myapp/instances/<agent-id>/skills"] }
}
```

## Error codes at the integration seams

[`docs/spec/envelope-and-errors.md`](https://github.com/microsoft/amplifier-agent/blob/main/docs/spec/envelope-and-errors.md) is the full registry, wire codes plus CLI-only codes. These are the ones raised at the seams a host owns: binary discovery, argv and config validation, the protocol handshake, approval, and session resume. The rest fire inside a turn.

| Code | Raised when | What to do |
|---|---|---|
| `binary_not_found` | The SDK resolves neither `AMPLIFIER_AGENT_BIN` nor `amplifier-agent` on `PATH` | Install the engine as the user that runs the host, or set `AMPLIFIER_AGENT_BIN` |
| `provider_not_configured` | No provider credentials resolvable at boot | Set the provider's env var or run `amplifier-agent auth set`; confirm with `doctor` |
| `approval_unconfigured` | Non-interactive, with no policy at any tier | Pass `-y` or `-n`, or set `approval.mode` in the host config |
| `argv_workspace_invalid` | `--workspace` fails the slug grammar `^[a-z0-9][a-z0-9-]{0,63}$` | Slugify tenant or project ids before passing them: lowercase, no leading `_`, 64 chars max |
| `config_unreadable`, `config_malformed_json` | The `--config` file could not be opened, or is not a JSON object | Check the path the host wrote, and that it serialized an object |
| `config_unknown_key` | Unrecognized **top-level** config key | The top level is closed: `approval`, `provider`, `providers`, `mcp`, `skills`, `debug`, `allowProtocolSkew` |
| `config_invalid_type` | A known key has the wrong type, or an unknown sub-key in a closed inner shape | `skills.*` and `debug.*` are closed and raise this rather than `config_unknown_key`, which is reserved for the top level and `providers.<id>` entries |
| `config_invalid_provider_module` | `provider.module` is not a known provider | One of `anthropic`, `openai`, `azure-openai`, `ollama`, `github-copilot`, `openai-chatgpt`. `"auto"` is not valid |
| `protocol_version_mismatch` | Wrapper and engine protocol versions differ | Update the lagging side. `allowProtocolSkew` is an unblock, not a fix |
| `lifecycle_unsupported` | `submit()` called twice on one handle | New handle per turn, same `sessionId` with `resume` |
| `env_injection_rejected` | The wrapper refused the environment you asked it to inject | Check the key against the wrapper's allowlist and blocked-key list |
| `approval_not_supported_in_v1` | An interactive approval callback was passed to the SDK | Use `approval: { mode: 'yes' \| 'no' }`; there is no callback channel yet |
| `session_not_found` | Resume names a session id with no transcript | Resume only after a first turn has persisted, or start with `--fresh` |
| `argv_mode_unknown` (exit 2), `unknown_mode` (HTTP 400) | `--mode`, or an `[amplifier-agent:mode=...]` directive, names a mode discovery did not find | Check `amplifier-agent modes list` or `GET /v1/modes` |
| `modes_unavailable` (exit 1, HTTP 503) | Mode discovery itself failed | An engine-side fault, not a bad mode name. Do not retry with a different name |
| `approval_denied`, `approval_timeout` | A tool call was declined, or no answer arrived in time | Expected under `-n` or a prompt policy; classification `approval`, exit 3 |

Exit codes: `0` success, `1` engine or transport, `2` protocol (skew, malformed argv, bad config), `3` approval runtime, `130` SIGINT. `2` and `3` are separable on purpose, so CI can gate on protocol failures and hosts can build deferral flows without parsing the envelope. When a parseable envelope is present it is authoritative; exit codes are informational.

## Checklist for a new integration

Install the engine and confirm with `doctor`; pin the protocol version; declare an approval policy; set `--workspace`; pass `--output json --display ndjson` and parse both streams; write one config file per instance; log the engine version and bundle digest from every envelope.
