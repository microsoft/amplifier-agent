# Amplifier Agent

**`amplifier-agent`** is a thin CLI wrapping the [Amplifier](https://github.com/microsoft/amplifier) kernel as a reactive stdio coprocess. Anything that can spawn a subprocess — a shell script, a Node app, a Python script, a chat bot, an IDE plugin — can use it as an agentic AI backend.

---

## What it is

A single binary that:

- **Accepts a prompt and returns a result** (Mode A, single-turn): `amplifier-agent run "your prompt"`
- **Speaks JSON-RPC over stdio for multi-turn conversations** (Mode B): `amplifier-agent run --stdio`

It is *not* a server, daemon, or long-lived service. Each invocation is a fresh process that exits when its caller closes stdin or sends `agent/shutdown`.

The engine library inside (`amplifier_agent_lib`) is transport-free Python that any Python app can also embed in-process — no subprocess needed.

## Why

Existing AI agent infrastructure assumes you're building a chat product. `amplifier-agent` is the opposite: it's an *engine you point other software at*. The CLI is the universal adapter — wherever you can shell out, you can use Amplifier.

The wire protocol intentionally mirrors [MCP](https://modelcontextprotocol.io/) (JSON-RPC over stdio, server-initiated bidirectional requests, capability negotiation) so existing host clients can integrate with minimal new infrastructure.

## Install

```bash
uv tool install amplifier-agent
amplifier-agent doctor       # verify environment
```

Other install methods:

- `pipx install amplifier-agent`
- From source: `git clone … && cd amplifier-agent && uv sync && uv tool install -e .`

First-run will prepare the built-in bundle and cache it to `$XDG_CACHE_HOME/amplifier-agent/`. Subsequent invocations skip this step.

## Quick start

Set a provider API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Run a one-shot turn:

```bash
amplifier-agent run "Summarize the README of github.com/microsoft/amplifier"
```

Or wire it into a host as a JSON-RPC subprocess:

```bash
amplifier-agent run --stdio
# Then write JSON-RPC requests to stdin, read events from stdout
```

## Provider configuration

Provider is auto-detected from environment variables in this precedence:

1. `ANTHROPIC_API_KEY`
2. `OPENAI_API_KEY`
3. `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`
4. `OLLAMA_HOST` (defaults to `http://localhost:11434`)

Override with `--provider <name>`. No `settings.yaml` to maintain.

## Modes

| Mode | Invocation | Caller | Lifecycle |
|---|---|---|---|
| **A** (single-turn) | `amplifier-agent run "prompt"` | Shell scripts, OpenClaw skills, ad-hoc CLI use | Spawn → init → one turn → exit |
| **B** (multi-turn stdio) | `amplifier-agent run --stdio` | Wrapper SDKs, conversational host adapters | Spawn → init → many turns → exit on EOF |

Both modes share the same engine; the only difference is who drives the I/O loop.

## Session continuity

```bash
# First turn
amplifier-agent run --session-id chat-42 "My favorite color is blue."

# Continue the conversation
amplifier-agent run --session-id chat-42 --resume "What did I say my favorite color was?"

# Start fresh in the same session ID (overwrites prior transcript)
amplifier-agent run --session-id chat-42 --fresh "Start over."
```

Sessions are persisted as transcript JSONL in `$XDG_STATE_HOME/amplifier-agent/sessions/<session-id>/`. Continuity is per-session-id, not per-process.

## Admin commands

```bash
amplifier-agent doctor              # Diagnose env, providers, paths, bundle cache
amplifier-agent config show         # Print resolved config with source annotations
amplifier-agent cache clear         # Invalidate the prepared-bundle cache
amplifier-agent --version           # Print version
```

## Approval flow

Some tools (file writes, command execution) request approval before acting. In single-turn mode:

- **Interactive terminal**: prompted on stderr; respond `y` / `N` / `c`
- **Non-interactive (CI, pipe, background)**: denied by default
- **Override**: `-y` accepts all, `-n` denies all (apt-style)

In stdio mode, approval flows over the wire as `approval/request` server-initiated JSON-RPC requests. Wrapper SDKs implement the host-side handler (callback, message-back, email, or anything else creative — adapter's choice).

## Embedding in your own Python host

Skip the CLI entirely if your host is Python:

```python
from amplifier_agent_lib import Engine
from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem, CliDisplaySystem

engine = await Engine.boot(
    approval_system=CliApprovalSystem(mode="auto"),
    display_system=CliDisplaySystem(verbosity="normal"),
)
result = await engine.submit_turn(prompt="Hello!", session_id="my-session")
await engine.shutdown()
```

See `src/amplifier_agent_lib/` for the full library surface.

## Architecture at a glance

amplifier-agent is one layer of the larger Amplifier ecosystem:

```
Host Application                              ← your code
    ↓
Adapter (host-specific glue)                  ← per-host integration
    ↓
Language Wrapper (TypeScript or Python)       ← typed SDK
    ↓ JSON-RPC over stdio (or in-process)
amplifier-agent CLI                           ← this repo
    ↓ (in-process)
amplifier_agent_lib (engine library)          ← this repo
    ↓
Amplifier Kernel (amplifier-core, amplifier-foundation)
```

The CLI binary (`amplifier-agent`) is a thin I/O adapter on top of `amplifier_agent_lib`. The library is transport-free — Python hosts can skip the subprocess entirely.

## Wire protocol (Mode B)

Mode B speaks JSON-RPC 2.0 over newline-delimited stdin/stdout:

| Method | Direction | Purpose |
|---|---|---|
| `agent/initialize` | Host → Agent | Capability negotiation |
| `session/create` | Host → Agent | Open a new session |
| `turn/submit` | Host → Agent | Submit a turn; agent streams notifications + returns result |
| `turn/cancel` | Host → Agent | Cancel an in-flight turn |
| `session/end` | Host → Agent | Close session and persist state |
| `agent/shutdown` | Host → Agent | Graceful exit |
| `approval/request` | Agent → Host | Request approval for a sensitive action |
| `notifications/*` | Agent → Host | Streaming events (`result/delta`, `result/final`, `tool/started`, `tool/completed`, `progress`, `thinking/*`, `usage`, `error`) |

Notifications are one-way (no `id`). Server-initiated requests use the same ID-correlation as host-initiated ones, just in reverse.

## Related repositories

- [`microsoft/amplifier`](https://github.com/microsoft/amplifier) — Top-level Amplifier project
- [`microsoft/amplifier-core`](https://github.com/microsoft/amplifier-core) — The kernel
- [`microsoft/amplifier-foundation`](https://github.com/microsoft/amplifier-foundation) — Bundle + module system
- [`microsoft/amplifier-app-cli`](https://github.com/microsoft/amplifier-app-cli) — Interactive REPL CLI for end users
- [`microsoft/amplifier-app-openclaw`](https://github.com/microsoft/amplifier-app-openclaw) — OpenClaw integration
- [`microsoft/amplifier-agent`](https://github.com/microsoft/amplifier-agent) — this repo

## Status

Phase 1 — Layer 4 (engine library + CLI) ships in this release. Roadmap:

- L3 language wrappers (TypeScript + Python SDKs) — designed, implementation next
- L2 host adapters (NanoClaw, Paperclip) — designed, implementation after L3
- Install paths, container packaging, full execution plan — deferred sections of the design checkpoint

See [`docs/designs/`](docs/designs/) and the [pull requests](https://github.com/microsoft/amplifier-agent/pulls) for design history and roadmap.

## Contributing

This project follows the Microsoft Open Source [Code of Conduct](CODE_OF_CONDUCT.md).

- Issues and PRs welcome.
- For security disclosures, see [`SECURITY.md`](SECURITY.md).
- For support guidance, see [`SUPPORT.md`](SUPPORT.md).

## License

MIT — see [`LICENSE`](LICENSE).

---

🤖 Built with [Amplifier](https://github.com/microsoft/amplifier).
