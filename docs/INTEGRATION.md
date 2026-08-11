# Integration guide

How to drive `amplifier-agent` from your own software.

The engine runs **one turn per invocation** and exits. Continuity across turns comes from a session ID, not from a long-lived process. Everything below is a different way of delivering a prompt to that same engine.

## Before you start

`amplifier-agent` is a standalone binary. You do not need the Amplifier CLI, bundles, or any other repository in the `microsoft/amplifier*` family, and none of them is a substitute for it here.

Use it when your software needs to run an agent: a loop with tools, file access, sub-agents, and/or multi-turn state. It also works for plain LLM calls, where you get routing across five providers behind one interface.

Then pick a surface below, install the engine ([INSTALL.md](INSTALL.md)), and finish with the [checklist](#checklist-for-a-new-integration).

## Pick a surface

| You are writing | Use | Section |
|---|---|---|
| Node.js or TypeScript | `amplifier-agent-ts` npm package | [TypeScript SDK](#typescript-sdk) |
| Python, separate process | `amplifier-agent-py` wrapper | [Python SDK](#python-sdk) |
| Python, same process | `amplifier_agent_lib` directly | [In-process library](#in-process-library) |
| Anything that speaks HTTP | `amplifier-agent serve chat-completions` | [HTTP face](#http-face) |
| A shell script, or a language with no SDK | The CLI contract | [Wire protocol](#wire-protocol) |

All five sit on the same engine.

## Prerequisites

The SDKs are **BYO-engine**: they have zero runtime dependencies and locate the `amplifier-agent` binary on `PATH`. Install the engine first ([INSTALL.md](INSTALL.md)), then the SDK for your language.

Install the engine as the same user that runs your host process. A host spawning a subprocess inherits that user's `PATH`.

## TypeScript SDK

```bash
npm install amplifier-agent-ts
```

Node.js 20 or later. Zero npm runtime dependencies.

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

`submit()` is one-shot per session and yields a typed `DisplayEvent` stream (`init`, `activity`, `result`, `error`). The SDK is a thin process supervisor: all inference, tool execution, and session state live in the Python engine, not in Node. Transport-level failures surface as `AaaError`.

Full API surface: [`wrappers/typescript/README.md`](../wrappers/typescript/README.md). Type definitions ship at `wrappers/typescript/dist/index.d.ts`.

## Python SDK

Use this when you want process isolation between your host and the engine. For same-process embedding, see [in-process library](#in-process-library) instead.

```python
from amplifier_agent_py import AaaError, spawn_agent_sync

with spawn_agent_sync(
    session_id="chat-42",
    display_mode="ndjson",
    approval={"mode": "yes"},
    env={"extra": {"ANTHROPIC_API_KEY": "sk-ant-..."}},
    timeout_ms=300_000,
) as handle:
    info = handle.get_engine_info()           # EngineInfo(engine_version, protocol_version)
    for event in handle.submit("Hello, agent."):
        if event.type == "result":
            print(event.text)
        elif event.type == "error":
            raise AaaError(event.code, event.message)
```

An async variant (`spawn_agent`, returning `SessionHandle`) is also exported. Runnable examples: [`wrappers/python-py/examples/`](../wrappers/python-py/examples/) contains `sync_chat.py`, `async_chat.py`, and `diagnostic.py`.

## In-process library

`amplifier_agent_lib` is transport-free Python. A Python host can import it and skip the subprocess entirely, giving up process isolation between host and engine. The CLI binary is itself a thin I/O adapter over this library, so the two paths share all engine behavior.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the layer boundaries and [`spec/engine-api.md`](spec/engine-api.md) for the library contract.

## HTTP face

```bash
amplifier-agent serve chat-completions
```

Exposes an OpenAI-compatible, bearer-authenticated HTTP surface. Use it when your host already speaks the OpenAI chat completions protocol. It is the surface `amplifier-app-opencode` uses.

Two routes mirror the CLI listings:

```
GET /v1/skills   {"object":"list","data":[{name,description,source,shadowed}]}
GET /v1/modes    {"object":"list","data":[{name,description,source,shadowed}]}
```

Select a mode over the wire with an `[amplifier-agent:mode=<name>]` directive in a `system` or `developer` message. User and assistant messages are ignored, so an echoed message cannot spoof it. The resolved mode comes back as a top-level `activeMode` field on every response, streaming and non-streaming, and is `null` when no mode is active. An unknown mode returns HTTP 400 (`code: "unknown_mode"`); a discovery failure returns HTTP 503 (`code: "modes_unavailable"`).

The `!amplifier:skill` sigil works here too, on the final `user` message only.

> [!WARNING]
> **The `approval` block has no effect on the HTTP path. Every tool call is auto-approved.** This is a security contract, not a footnote: the chat-completions wire has no human-in-the-loop channel, so a host relying on `approval.mode` for safety gets no protection from this face. Isolate the server accordingly. See [`spec/http-face.md`](spec/http-face.md).

Model ids on this surface are namespaced per provider, because a single model list spans every enabled provider. See [CONFIGURATION.md](CONFIGURATION.md#model-ids). Normative contract: [`spec/http-face.md`](spec/http-face.md).

## Wire protocol

Protocol version **`0.3.0`**, defined in `src/amplifier_agent_lib/protocol/methods.py`. Breaking changes bump it. Wrappers must pass `--protocol-version 0.3.0`; a mismatch returns `protocol_version_mismatch` and exits non-zero rather than silently misbehaving.

The wrapper passes flags as argv. The engine writes one JSON envelope line to stdout on completion.

**Input, selected argv flags:**

| Flag | Type | Purpose |
|---|---|---|
| `PROMPT` | positional | The turn prompt |
| `--session-id` | str | Session ID for continuity |
| `--workspace` | str | Workspace name for isolating session state |
| `--resume` | flag | Resume from saved transcript |
| `--fresh` | flag | Discard saved state and start over |
| `--protocol-version` | str | Wrapper's pinned protocol version; engine validates match |
| `--config` | path | Host config file (provider override, approval policy, and more) |
| `--cwd` | path | Working directory for the agent. Defaults to the launch directory, which is what makes `<launch-dir>/.amplifier/modes` discoverable |
| `--mode` | str | Per-turn mode to activate (non-sticky); re-pass each turn to persist, omit to disable |
| `-y` / `-n` | flag | Auto-approve or auto-deny all approval requests (mutually exclusive) |
| `--output` | text \| json | stdout mode (default `text`, reply only) |
| `--display` | text \| ndjson | stderr mode (default `text`; wrappers pass `ndjson`) |

**Output**, stdout under `--output json`, a single JSON line:

```json
{
  "protocolVersion": "0.3.0",
  "sessionId": "...",
  "turnId": "turn-1",
  "reply": "...",
  "error": null,
  "metadata": {
    "tokensIn": 0, "tokensOut": 0, "durationMs": 0,
    "bundleDigest": "...", "engineVersion": "...",
    "protocolVersion": "0.3.0", "correlationId": "...",
    "activeMode": null
  }
}
```

`activeMode` echoes the `--mode` value for the turn, `null` when omitted. Under `--output text` (the default) stdout is the reply text only, which is easier to pipe into shell tooling.

**Streams are strictly separated.** Diagnostic events (tool calls, thinking, progress) go to **stderr** only. Stdout carries the envelope or reply so callers can parse it without filtering. Under `--display ndjson`, stderr emits one JSON-RPC notification per line for wrapper consumption.

Normative contracts: [`spec/wire-protocol.md`](spec/wire-protocol.md), [`spec/envelope-and-errors.md`](spec/envelope-and-errors.md), [`spec/wrapper-contract.md`](spec/wrapper-contract.md).

## Session continuity

Sessions persist as transcript JSONL under `$AMPLIFIER_AGENT_HOME/state/workspaces/<workspace>/sessions/<session-id>/`. Continuity is per `(workspace, session-id)` pair.

```bash
amplifier-agent run -y --session-id chat-42 "My favorite color is blue."
amplifier-agent run -y --session-id chat-42 --resume "What did I say my favorite color was?"
amplifier-agent run -y --session-id chat-42 --fresh "Start over."
```

`--resume` and `--fresh` are mutually exclusive. Passing both exits with `Error: --resume and --fresh are mutually exclusive`.

Pass `--workspace <name>` to isolate session state per project. Without it, sessions are scoped to the current working directory, which means a host that spawns from varying directories will see its sessions fragment. Multi-tenant hosts should always set `--workspace` explicitly.

## Approval policy

A host that spawns the engine headlessly **must declare an approval policy**, or the run refuses to start: `-y`, `-n`, or `approval.mode` in a host config file. With none of those and no TTY, the run exits 2 with `approval_unconfigured` rather than auto-denying every tool call and still exiting 0, which looked like success while doing no work.

Both SDKs accept an approval option and pass it through, so you normally set it there rather than on argv. Full precedence in [CONFIGURATION.md](CONFIGURATION.md#approval-policy).

## Per-instance configuration

A subprocess host typically writes one config file per agent instance and passes `--config <path>` on every turn:

```json
{
  "approval": { "mode": "yes" },
  "provider": { "module": "anthropic", "config": { "default_model": "claude-sonnet-5" } },
  "mcp": { "configPath": "/var/run/paperclip/instances/<agent-id>/mcp.json" },
  "skills": { "skills": ["/var/run/paperclip/instances/<agent-id>/skills"] }
}
```

This is the standard pattern for multi-tenant hosts: one directory per agent, holding its MCP server list, its skills, and its config. The top level of the file is closed, so an unknown key is an error rather than a warning. Full schema in [CONFIGURATION.md](CONFIGURATION.md) and [`spec/host-config.md`](spec/host-config.md).

## Checklist for a new integration

1. Install the engine as the user that runs your host, and confirm with `amplifier-agent doctor`.
2. Pin `--protocol-version` to the version your SDK targets. Fail loudly on mismatch.
3. Declare an approval policy explicitly, and never rely on the TTY fallback in a service. On the HTTP face the policy is inert and every tool call is auto-approved, so isolate that server instead.
4. Set `--workspace` so session state does not follow the working directory.
5. Pass `--output json --display ndjson` and parse both streams. Never parse stderr for results.
6. Write one host config file per agent instance.
7. Record `metadata.engineVersion` and `metadata.bundleDigest` from the envelope alongside your own logs.

## Reference

- Architecture and layer boundaries: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Normative specifications index: [`SPEC.md`](SPEC.md)
- Configuration: [`CONFIGURATION.md`](CONFIGURATION.md)
- Command and flag reference: [`CLI.md`](CLI.md)
- Applications already integrated: [`ECOSYSTEM.md`](ECOSYSTEM.md)
