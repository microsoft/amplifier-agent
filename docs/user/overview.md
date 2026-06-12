# Overview

**amplifier-agent** is a command-line AI coding agent. It boots an opinionated bundle (a fixed orchestrator, context, hooks, and a set of sub-agents), submits a single prompt to a configured LLM provider, executes any tool calls the model requests (after approval), prints the result, and exits.

## What it does

```
$ amplifier-agent run "List the python files in src/" -y
[result/delta] Here are the python files...

$ amplifier-agent run "What was your last reply?" --resume --session-id smoke-1 -y
[result/delta] My last reply was "ping".
```

Each invocation of `amplifier-agent run` is a **single turn**: one prompt in, one reply out. State is persisted to disk between turns under a *workspace* and a *session id*, so successive runs can carry forward conversation history with `--resume`.

## Where it fits

amplifier-agent is one of three layers built on the [amplifier-core](https://github.com/microsoft/amplifier-core) kernel and the [amplifier-foundation](https://github.com/microsoft/amplifier-foundation) library:

| Layer | What it is |
|---|---|
| amplifier-core | The thin kernel. Defines module contracts (providers, tools, orchestrators, hooks, context) and runs a session. |
| amplifier-foundation | The composition library. Bundles, behaviors, agent files, the `delegate` pattern. |
| **amplifier-agent** | **An opinionated CLI built on the foundation. Vendored bundle, hard-coded sub-agents, single-turn execution model.** |

The CLI vendors a bundle manifest at install time ([`amplifier-agent-builtin` v1.3.0](../../src/amplifier_agent_lib/bundle/bundle.md)) and four sub-session agents (`explorer`, `planner`, `coder`, `tester`). When the model decides to delegate to a sub-agent (via the `delegate` tool), the CLI spawns it as a child session with its own tool surface.

## Architecture

```
┌─ amplifier-agent (CLI process) ─────────────────────────────────┐
│                                                                 │
│   click ──> single_turn.run() ──> engine.boot() ──> core loop   │
│                                          │                      │
│                                          ├─ provider (LLM)      │
│                                          ├─ tools (todo,        │
│                                          │   delegate, mcp,     │
│                                          │   skills)            │
│                                          ├─ hooks (status,      │
│                                          │   redaction,         │
│                                          │   logging)           │
│                                          └─ context-simple      │
│                                                                 │
│   stdout: JSON envelope or plain reply (--output)               │
│   stderr: NDJSON wire events or human-readable text (--display) │
└─────────────────────────────────────────────────────────────────┘
              │ writes
              v
~/.amplifier-agent/state/workspaces/<ws>/sessions/<id>/
    transcript.jsonl
    metadata.json
    audits/turn-<id>.json
    context-intelligence/events.jsonl
```

The engine is launched fresh for every `run`. There is no daemon, no persistent server. Sessions are persisted **between** runs, but each run is a clean subprocess that loads the prepared bundle (from a pickle cache), wires up the configured provider, and exits after one turn.

## Two consumer surfaces

1. **The CLI** — typed by humans, scripts, and CI. Documented in [CLI reference](cli-reference.md).

2. **The TypeScript SDK** (`amplifier-agent-ts`) — used by hosts (IDE extensions, chat UIs, agents-of-agents) to spawn the CLI per turn and consume its structured event stream. Documented in [TypeScript SDK](../typescript/overview.md).

Both surfaces talk to the *same* `amplifier-agent` binary. The SDK is a thin process driver; it does not contain LLM logic. Anything you can do via the SDK, you can do by invoking the CLI directly.

## What it is not

- **Not a server.** No daemon, no socket, no long-lived process. One run = one subprocess.
- **Not a multi-turn REPL.** The CLI is single-turn. Multi-turn conversation is achieved by re-invoking with `--resume` and the same `--session-id`. Hosts wanting an interactive feel drive this loop themselves.
- **Not a bundle host.** The bundle is vendored and sealed per release. You cannot swap providers, tools, or orchestrators at runtime. You *can* parameterize what the bundle exposes — see [Configuration](configuration.md).
- **Not a generic Amplifier shell.** For exploratory multi-bundle development, use the broader amplifier ecosystem (`amplifier-foundation`, your own CLI built on top). amplifier-agent is the opinionated, sealed user-facing distribution.

## Next steps

- New here? → [Quickstart](quickstart.md)
- Building a host? → [TypeScript SDK overview](../typescript/overview.md)
- Want every flag? → [CLI reference](cli-reference.md)
