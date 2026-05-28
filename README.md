# Amplifier Agent

**`amplifier-agent`** is a thin CLI wrapping the [Amplifier](https://github.com/microsoft/amplifier) kernel as a reactive stdio coprocess. Anything that can spawn a subprocess — a shell script, a Node app, a Python script, a chat bot, an IDE plugin — can use it as an agentic AI backend.

---

## What it is

A single binary that:

- **Accepts a prompt and returns a result** (single-turn): `amplifier-agent run "your prompt"`
- **Emits one JSON envelope on stdout per invocation** — wrappers spawn one process per turn and pass `--session-id` for continuity

It is *not* a server, daemon, or long-lived service. Each invocation is a fresh process that runs one turn and exits. Multi-turn conversations are managed at the wrapper or session-ID layer — not inside a persistent process.

The engine library inside (`amplifier_agent_lib`) is transport-free Python that any Python app can also embed in-process — no subprocess needed.

## Why

Existing AI agent infrastructure assumes you're building a chat product. `amplifier-agent` is the opposite: it's an *engine you point other software at*. The CLI is the universal adapter — wherever you can shell out, you can use Amplifier.

The Mode A wire protocol is intentionally simple: the engine takes a single invocation (argv + env), runs one turn, and writes one JSON result envelope to stdout. Wrapper SDKs (TypeScript and Python) handle spawning, result parsing, and session continuity on top.

## Install

The Python engine is not yet published to any package registry. Install from source:

```bash
git clone https://github.com/microsoft/amplifier-agent.git
cd amplifier-agent
uv sync
uv tool install -e .
amplifier-agent doctor       # verify environment
```

Engine and wrapper releases are tagged separately — `engine-v0.3.0` and `wrapper-v0.4.0` respectively. Check out a specific tag (`git checkout engine-v0.3.0`) before `uv tool install -e .` if you need a pinned version. Run `git tag -l` to see the full list.

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

The TypeScript and Python wrapper SDKs handle subprocess management automatically. See `wrappers/typescript/` (`amplifier-agent-ts` on npm) and `wrappers/python/` for ready-to-use clients.

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
| **A** (single-turn) | `amplifier-agent run "prompt"` | Shell scripts, wrapper SDKs, host adapters, ad-hoc CLI use | Spawn → one turn → JSON envelope on stdout → exit |

Multi-turn conversations are built by the caller: spawn one process per turn, passing `--session-id` for continuity. A persistent stdio JSON-RPC mode (Mode B) was originally designed but was superseded by the Mode A subprocess driver in PR #8 (see `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md`).

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
amplifier-agent prepare             # Pre-warm bundle cache (run once after install)
amplifier-agent verify              # Verify install integrity
amplifier-agent config show         # Print resolved config with source annotations
amplifier-agent cache clear         # Invalidate the prepared-bundle cache
amplifier-agent --version           # Print version
```

## Approval flow

Some tools (file writes, command execution) request approval before acting:

- **Interactive terminal**: prompted on stderr; respond `y` to approve, anything else to decline
- **Non-interactive (CI, pipe, background)**: denied by default
- **Override**: `-y` accepts all, `-n` denies all (apt-style)

Wrapper SDKs can install their own approval handler (callback, message-back, email, or anything else creative — adapter's choice) via the `ApprovalSystem` protocol point on the engine library.

## Embedding in your own Python host

Skip the CLI entirely if your host is Python:

```python
import sys
from amplifier_agent_lib import __version__
from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.engine import Engine
from amplifier_agent_lib.protocol import PROTOCOL_VERSION
from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem, CliDisplaySystem

prepared = await load_and_prepare_cached(aaa_version=__version__)
handler = make_turn_handler(prepared, cwd=None, is_resumed=False, mcp_config_path=None)

engine = Engine(
    turn_handler=handler,
    protocol_points={
        "approval": CliApprovalSystem(mode="no"),
        "display": CliDisplaySystem(verbosity="normal", stream=sys.stderr),
    },
)
await engine.boot({
    "protocolVersion": PROTOCOL_VERSION,
    "clientInfo": {"name": "my-host", "version": "0.1.0"},
    "capabilities": {},
    "sessionId": "my-session",
    "resume": False,
})
result = await engine.submit_turn({
    "sessionId": "my-session",
    "turnId": "turn-1",
    "prompt": "Hello!",
})
await engine.shutdown()
```

`Engine.boot()` is an instance method that takes a params dict. The constructor requires both `turn_handler` (built from a `PreparedBundle`) and the `protocol_points` dict. See `src/amplifier_agent_lib/` for the full library surface.

## TypeScript / Node.js SDK

For Node.js and TypeScript hosts, use the `amplifier-agent-ts` npm package. It is a thin process supervisor that spawns the Python `amplifier-agent` CLI per turn (Mode A) and exposes a typed async API — all inference, tool execution, and session state live in the Python engine.

You need **both** packages installed: the npm SDK *and* the Python engine (see [Install](#install) above — the Python CLI must be on `PATH`).

```bash
npm install amplifier-agent-ts
```

```typescript
import { spawnAgent, AaaError } from 'amplifier-agent-ts';
import { randomUUID } from 'node:crypto';

const session = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: randomUUID(),
});

try {
  const result = await session.submit({ prompt: 'Hello, agent.' });
  console.log(result.reply);
} catch (err) {
  if (err instanceof AaaError) {
    console.error(`[${err.code}] ${err.message}`);
  } else {
    throw err;
  }
}
```

Requires Node.js ≥ 20. Zero npm runtime dependencies. Full API surface in [`wrappers/typescript/README.md`](wrappers/typescript/README.md) and the type definitions at `wrappers/typescript/dist/index.d.ts`. The Python sibling SDK lives at [`wrappers/python/`](wrappers/python/).

## Architecture at a glance

amplifier-agent is one layer of the larger Amplifier ecosystem:

```
Host Application                              ← your code
    ↓
Adapter (host-specific glue)                  ← per-host integration
    ↓
Language Wrapper (TypeScript or Python)       ← typed SDK
    ↓ subprocess (argv in / JSON envelope out, or in-process)
amplifier-agent CLI                           ← this repo
    ↓ (in-process)
amplifier_agent_lib (engine library)          ← this repo
    ↓
Amplifier Kernel (amplifier-core, amplifier-foundation)
```

The CLI binary (`amplifier-agent`) is a thin I/O adapter on top of `amplifier_agent_lib`. The library is transport-free — Python hosts can skip the subprocess entirely.

## Wire protocol (Mode A)

Protocol version: **`0.2.0`** (defined in `src/amplifier_agent_lib/protocol/methods.py`; breaking changes bump this). Wrappers must pass `--protocol-version 0.2.0` — version mismatches return a `protocol_version_mismatch` error and exit non-zero rather than silently misbehave.

Mode A uses a single subprocess invocation per turn. The wrapper passes flags as argv; the engine writes one JSON envelope line to stdout on completion.

**Input (selected argv flags):**

| Flag | Type | Purpose |
|---|---|---|
| `PROMPT` | positional | The turn prompt |
| `--session-id` | str | Session ID for continuity |
| `--resume` | flag | Resume from saved transcript |
| `--fresh` | flag | Discard saved state and start over |
| `--protocol-version` | str | Wrapper's pinned protocol version; engine validates match |
| `--mcp-config-path` | path | Path to MCP server config JSON (written by the wrapper) |
| `--host-capabilities` | JSON | Host capability advertisement |
| `-y` / `-n` | flag | Auto-approve / auto-deny all approval requests |
| `--output` | text \| json | `json` (default) emits full envelope; `text` emits the reply only |

**Output (stdout, single JSON line):**

```json
{
  "protocolVersion": "0.2.0",
  "sessionId": "...",
  "turnId": "turn-1",
  "reply": "...",
  "error": null,
  "metadata": {
    "tokensIn": 0, "tokensOut": 0, "durationMs": 0,
    "bundleDigest": "...", "engineVersion": "...",
    "protocolVersion": "0.2.0", "correlationId": "..."
  }
}
```

Diagnostic events (tool calls, thinking, progress) go to **stderr** only — stdout is reserved for the single envelope so callers can parse it with `JSON.parse(line)` without filtering.

The TypeScript and Python wrapper SDKs (`wrappers/typescript/`, `wrappers/python/`) handle all of this: they spawn `amplifier-agent run`, write the MCP config tmpfile, parse the envelope, and expose a typed async API.

## Related repositories

- [`microsoft/amplifier`](https://github.com/microsoft/amplifier) — Top-level Amplifier project
- [`microsoft/amplifier-core`](https://github.com/microsoft/amplifier-core) — The kernel
- [`microsoft/amplifier-foundation`](https://github.com/microsoft/amplifier-foundation) — Bundle + module system
- [`microsoft/amplifier-app-cli`](https://github.com/microsoft/amplifier-app-cli) — Interactive REPL CLI for end users
- [`microsoft/amplifier-app-openclaw`](https://github.com/microsoft/amplifier-app-openclaw) — OpenClaw integration
- [`microsoft/amplifier-agent`](https://github.com/microsoft/amplifier-agent) — this repo

## Status

Current versions: engine `0.3.0`, TypeScript wrapper `amplifier-agent-ts@0.4.0`, Python wrapper `amplifier_agent_client` (see `wrappers/python/`). Wire protocol: `0.2.0`.

**Shipped:**

- L4 — Engine library + CLI (Mode A)
- L3 — TypeScript + Python wrapper SDKs (`amplifier-agent-ts` on npm, `amplifier_agent_client` in `wrappers/python/`)
- Protocol schemas + cross-language conformance test suite (`wrappers/conformance/`)
- Path-based MCP config delivery (`--mcp-config-path`)

**In progress / next:**

- L2 — Host adapters (NanoClaw, Paperclip) — see `docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md`
- Container packaging, install-path finalization

See [`docs/designs/`](docs/designs/) and the [pull requests](https://github.com/microsoft/amplifier-agent/pulls) for design history and roadmap. The Mode A pivot is captured in [`docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md`](docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md).

## Contributing

This project follows the Microsoft Open Source [Code of Conduct](CODE_OF_CONDUCT.md).

- Issues and PRs welcome.
- For security disclosures, see [`SECURITY.md`](SECURITY.md).
- For support guidance, see [`SUPPORT.md`](SUPPORT.md).

## License

MIT — see [`LICENSE`](LICENSE).

---

🤖 Built with [Amplifier](https://github.com/microsoft/amplifier).
