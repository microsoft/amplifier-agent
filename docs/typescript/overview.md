# TypeScript SDK overview

**`amplifier-agent-ts`** is the TypeScript SDK for hosts that want to embed amplifier-agent: IDE extensions, chat UIs, web servers, agent-of-agents systems.

It is a **process driver**, not an LLM client. It spawns the `amplifier-agent` Python binary for each turn, drives its argv and environment, and consumes its structured event stream.

```
┌─ Your host (Node.js / Electron / VS Code extension) ─────────────────────┐
│                                                                          │
│   import { spawnAgent } from 'amplifier-agent-ts';                       │
│                                                                          │
│   const handle = await spawnAgent({                                      │
│     lifecycle: 'one-shot', sessionId: 's1', resume: false,               │
│     approval: { mode: 'yes' }, displayMode: 'ndjson',                    │
│     env: { allowlist: [...DEFAULT_ALLOWLIST, 'ANTHROPIC_API_KEY'] },     │
│   });                                                                    │
│                                                                          │
│   for await (const event of handle.submit('Hello')) {                    │
│     // DisplayEvent: init | activity | result | error | notification     │
│   }                                                                      │
│                                                                          │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │ child_process.spawn(amplifier-agent, [...argv])
                     v
        amplifier-agent run --session-id s1 --fresh --output json
                          --display ndjson --protocol-version 0.3.0 -y
                          "Hello"
                     │
                     ├─ stdout: {<JSON envelope>}\n
                     └─ stderr: {<wire event>}\n × N
```

## Subprocess per turn

The SDK launches a **fresh subprocess for every `submit()` call**. There is no daemon, no IPC channel beyond the engine's stdout/stderr. This matches the engine's single-turn model.

Implications:

- **`spawnAgent()` does not spawn a subprocess.** It validates parameters, resolves the binary, builds the env, and constructs a `SessionHandle`. The subprocess is spawned when you call `submit()`.
- **`SessionHandle.submit()` is single-use.** Call it once per `SessionHandle`. To send a second turn, create a new `SessionHandle` with the same `sessionId` and pass `resume: true`.
- **`SessionHandle.cancel()` SIGTERMs the engine.** Five seconds later, if still alive, SIGKILL. The engine is the session leader (`setsid`), so MCP child processes get group-killed too.

## What the SDK does for you

| Concern | What the SDK handles |
|---|---|
| Binary discovery | `AMPLIFIER_AGENT_BIN` env, then `which amplifier-agent`, with a clear error if neither resolves. |
| Version compatibility | Probes the engine at spawn time, compares to `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` (`"0.3.0"`), fails fast on mismatch (overridable). |
| Argv assembly | Builds the canonical argv from typed parameters. No string-bashing on the caller's side. |
| Env hygiene | Allowlist-based env passthrough. Blocks dangerous keys (`PYTHONPATH`, `LD_PRELOAD`, etc.). |
| MCP config spill | Accepts an `mcpServers` object in your params; writes a `0600` tempfile and points the engine at it via `AMPLIFIER_MCP_CONFIG`. Cleans up on cancel. |
| NDJSON parsing | Reads the engine's stderr stream and yields one `notification` event per JSON line. |
| Stdout envelope parsing | Reads the engine's stdout envelope, surfaces success as `result`, errors as `error`. Synthesizes a fallback error event from exit code + stderr tail if the envelope is missing. |
| Activity pings | Yields an `activity` event every 2 seconds while the engine is alive, so your UI knows it hasn't hung. |
| Cancel / dispose | Graceful SIGTERM → SIGKILL on a process group. Tempfile cleanup. |

## When to use the SDK vs the CLI directly

| Use the SDK | Use the CLI |
|---|---|
| You're writing a Node/Electron/VS Code app | You're in a shell, CI, or another non-Node language |
| You want typed events and error classifications | You want the simplest possible invocation |
| You need MCP servers configured per-call | You manage MCP via `AMPLIFIER_MCP_CONFIG` yourself |
| You want to drive cancel/timeout from your UI | You can `kill -TERM` yourself |

Anything the SDK does, you can do by invoking the CLI directly with `child_process.spawn`. The SDK is convenience and consistency, not capability.

## Read next

- [Quickstart](quickstart.md) — `npm install` and a hello-world.
- [API reference](api-reference.md) — every public export.
- [Events](events.md) — the `DisplayEvent` union and wire events.
- [Advanced](advanced.md) — approval handling, MCP, env allowlist, custom binaries, models list.
