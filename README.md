# Amplifier Agent

**`amplifier-agent`** is a thin CLI wrapping the [Amplifier](https://github.com/microsoft/amplifier) kernel as a per-turn stdio subprocess. Anything that can spawn a subprocess — a shell script, a Node app, a Python script, a chat bot, an IDE plugin — can use it as an agentic AI backend.

---

## What it is

A single binary that:

- **Accepts a prompt and returns a result** (one turn per invocation): `amplifier-agent run -y "your prompt"`
- **Emits one JSON envelope on stdout per invocation** when `--output json` is set — wrappers spawn one process per turn and pass `--session-id` for continuity

It is *not* a server, daemon, or long-lived service. Each invocation is a fresh process that runs one turn and exits. Multi-turn conversations are managed at the wrapper or session-ID layer — not inside a persistent process.

The engine library inside (`amplifier_agent_lib`) is transport-free Python that any Python app can also embed in-process — no subprocess needed.

## Why

Existing AI agent infrastructure assumes you're building a chat product. `amplifier-agent` is the opposite: it's an *engine you point other software at*. The CLI is the universal adapter — wherever you can shell out, you can use Amplifier.

The wire protocol is intentionally simple: the engine takes a single invocation (argv + env), runs one turn, and writes one JSON result envelope to stdout. Wrapper SDKs (TypeScript and Python) handle spawning, result parsing, and session continuity on top.

## Install

The Python engine is not yet published to a package registry, but `uv tool install` installs it directly from git:

```bash
uv tool install git+https://github.com/microsoft/amplifier-agent
amplifier-agent doctor       # verify environment
```

Pin to a specific engine release by appending a tag:

```bash
uv tool install git+https://github.com/microsoft/amplifier-agent@engine-v0.6.0
```

Engine and wrapper releases are tagged separately (`engine-v*`, `wrapper-v*`, `wrapper-py-v*`). For local development against a checkout, `git clone` the repo and run `uv tool install -e .` from inside it.

First-run prepares the built-in bundle and caches it under `$AMPLIFIER_AGENT_HOME` (defaults to `~/.amplifier-agent/`). Subsequent invocations skip this step.

To update an existing install in place:

```bash
amplifier-agent update           # install the latest release
amplifier-agent update --check   # report-only; don't install
```

## Quick start

Set a provider API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Run a one-shot turn (the `-y` auto-approves tool calls — required in headless mode; see [Approval flow](#approval-flow)):

```bash
amplifier-agent run -y "Summarize the README of github.com/microsoft/amplifier"
```

The TypeScript and Python wrapper SDKs handle subprocess management and approval policy automatically. See [`wrappers/typescript/`](wrappers/typescript/) (`amplifier-agent-ts` on npm) and [`wrappers/python-py/`](wrappers/python-py/) for ready-to-use clients.

## Provider configuration

Provider is auto-detected from environment variables in this precedence:

1. `ANTHROPIC_API_KEY`
2. `OPENAI_API_KEY`
3. `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`
4. `OLLAMA_HOST` (defaults to `http://localhost:11434`)

Override with `--config <path-to-yaml>` pointing at a host config file that sets a provider explicitly. There is no implicit `settings.yaml`.

> **Deprecated alias:** `AZURE_OPENAI_KEY` (without `_API_`) is still accepted as a fallback for backwards compatibility and triggers a one-time stderr warning when used. Prefer `AZURE_OPENAI_API_KEY` — the legacy name will be removed in a future release.

To enumerate available models from a provider:

```bash
amplifier-agent models list                       # aggregate across all configured providers
amplifier-agent models list --provider anthropic  # one provider only
amplifier-agent models list --latest              # surface only the newest of each family
```

## Credential management

For users who prefer "set once, works everywhere" over editing shell rc files, amplifier-agent ships an `auth` subcommand that persists provider credentials at `~/.amplifier-agent/credentials.json` (mode `0600`):

```bash
amplifier-agent auth set anthropic    sk-ant-...
amplifier-agent auth set openai       sk-...
amplifier-agent auth set azure-openai sk-... --endpoint https://...
amplifier-agent auth list             # show configured providers (api keys masked)
amplifier-agent auth status           # diagnose env-vs-file precedence per provider
amplifier-agent auth remove openai    # delete a single entry
amplifier-agent auth clear --force    # delete the whole file
```

Resolution order is **env-first** so existing shell-rc workflows keep working unchanged:

1. Shell environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) — wins when set
2. `~/.amplifier-agent/credentials.json` — fallback for "set once" UX
3. Empty — caller decides whether the missing credential is an error or a no-op

This matters for wrappers like `amplifier-opencode` that spawn `amplifier-agent` as a subprocess: once you've run `amplifier-agent auth set anthropic ...` once, every subsequent invocation — from any terminal, from any directory, with or without exported env vars — picks the key up automatically.

The file format is a versioned JSON envelope:

```jsonc
{
  "version": 1,
  "providers": {
    "anthropic":    { "api_key": "sk-ant-..." },
    "openai":       { "api_key": "sk-..." },
    "azure-openai": { "api_key": "...", "endpoint": "https://..." }
  }
}
```

Unknown providers and unknown fields round-trip through reads/writes so future amplifier-agent releases can extend the schema without dropping pre-existing user configuration. The file is plaintext (matching `aws credentials`, `gh hosts.yml`, `claude/credentials.json`) — OS keychain integration is a future concern.

## Session continuity

```bash
# First turn
amplifier-agent run -y --session-id chat-42 "My favorite color is blue."

# Continue the conversation
amplifier-agent run -y --session-id chat-42 --resume "What did I say my favorite color was?"

# Start fresh in the same session ID (overwrites prior transcript)
amplifier-agent run -y --session-id chat-42 --fresh "Start over."
```

`--resume` and `--fresh` are mutually exclusive; passing both exits with `Error: --resume and --fresh are mutually exclusive`.

Sessions are persisted as transcript JSONL under `$AMPLIFIER_AGENT_HOME/state/workspaces/<workspace>/sessions/<session-id>/`. Continuity is per-(workspace, session-id) — pass `--workspace <name>` to isolate session state by project. Without `--workspace`, sessions are scoped to the current working directory.

## Output and display modes

Two independent flags govern what goes where:

| Flag | Controls | Values | Default |
|---|---|---|---|
| `--output` | **stdout** | `text` (reply only) \| `json` (full envelope) | `text` |
| `--display` | **stderr** | `text` (human-readable summaries) \| `ndjson` (one JSON-RPC notification per line) | `text` |

Wrappers always pass `--output json --display ndjson` explicitly. Humans typically want the defaults (`text` / `text`). `--verbose`, `--debug`, and `--quiet` further tune the human-readable stderr stream and are ignored under `--display ndjson`.

## Admin commands

```bash
amplifier-agent doctor              # Diagnose env, providers, paths, bundle cache
amplifier-agent prepare             # Pre-warm the bundle cache (run once after install)
amplifier-agent verify              # Verify install integrity and hook coverage
amplifier-agent version             # Engine version and wire protocol version
amplifier-agent --version           # Engine version only (Click-standard)
amplifier-agent config show         # Print resolved config with source annotations
amplifier-agent cache clear         # Invalidate the prepared-bundle cache
amplifier-agent migrate             # Migrate legacy storage layouts to current
amplifier-agent models list         # Enumerate available models from providers
amplifier-agent update              # Check for and install the latest release
```

Migrations are user-invoked. The engine refuses to run against an outdated storage layout and points you at `migrate` — it does not auto-migrate at boot.

## Approval flow

Some tools (file writes, command execution) request approval before acting:

- **Interactive terminal**: prompted on stderr; respond `y` to approve, anything else to decline
- **Non-interactive (CI, pipe, background)**: **the engine refuses to run unless an explicit policy is set.** Pass `-y` (auto-approve), `-n` (auto-deny), or supply a host config with `approval.mode` set to `yes`, `no`, or `prompt`. The engine no longer silently auto-denies — that produced success-shaped no-op runs that masked real bugs.
- **`-y` and `-n` are mutually exclusive.**

Wrapper SDKs install their own approval handler (callback, message-back, email, or anything else creative — adapter's choice) via the `ApprovalSystem` protocol point on the engine library. The TS and Python wrappers set `approval={"mode": "yes"}` (or equivalent) by default.

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
        "approval": CliApprovalSystem(mode="yes"),  # or "no" / "prompt"
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

`Engine.boot()` is an instance method that takes a params dict. The constructor requires both `turn_handler` (built from a `PreparedBundle`) and the `protocol_points` dict — including an explicit `approval` policy. See `src/amplifier_agent_lib/` for the full library surface.

## TypeScript / Node.js SDK

For Node.js and TypeScript hosts, use the `amplifier-agent-ts` npm package. It is a thin process supervisor that spawns the Python `amplifier-agent` CLI per turn and exposes a typed async API — all inference, tool execution, and session state live in the Python engine.

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

Requires Node.js ≥ 20. Zero npm runtime dependencies. Full API surface in [`wrappers/typescript/README.md`](wrappers/typescript/README.md) and the type definitions at `wrappers/typescript/dist/index.d.ts`.

## Python SDK

For Python hosts that want a typed wrapper instead of embedding the library directly, use [`amplifier-agent-py`](wrappers/python-py/). It is BYO-engine: the wrapper has zero runtime dependencies and discovers the `amplifier-agent` binary on `PATH`.

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

An async variant (`spawn_agent` returning `SessionHandle`) is also exported. See [`wrappers/python-py/examples/`](wrappers/python-py/examples/) for `sync_chat.py`, `async_chat.py`, and `diagnostic.py`.

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

## Wire protocol

Protocol version: **`0.3.0`** (defined in `src/amplifier_agent_lib/protocol/methods.py`; breaking changes bump this). Wrappers must pass `--protocol-version 0.3.0` — version mismatches return a `protocol_version_mismatch` error and exit non-zero rather than silently misbehave.

The engine is invoked once per turn. The wrapper passes flags as argv; the engine writes one JSON envelope line to stdout on completion.

**Input (selected argv flags):**

| Flag | Type | Purpose |
|---|---|---|
| `PROMPT` | positional | The turn prompt |
| `--session-id` | str | Session ID for continuity |
| `--workspace` | str | Workspace name for isolating session state |
| `--resume` | flag | Resume from saved transcript |
| `--fresh` | flag | Discard saved state and start over |
| `--protocol-version` | str | Wrapper's pinned protocol version; engine validates match |
| `--config` | path | Host config YAML (provider override, approval policy, etc.) |
| `--cwd` | path | Working directory for the agent |
| `-y` / `-n` | flag | Auto-approve / auto-deny all approval requests (mutually exclusive) |
| `--output` | text \| json | stdout mode (default `text` — reply only) |
| `--display` | text \| ndjson | stderr mode (default `text`; wrappers pass `ndjson`) |

**Output (stdout under `--output json`, single JSON line):**

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
    "protocolVersion": "0.3.0", "correlationId": "..."
  }
}
```

Under `--output text` (the default), stdout is the reply text only — easier to pipe into shell tooling.

Diagnostic events (tool calls, thinking, progress) go to **stderr** only — stdout is reserved for the envelope/reply so callers can parse it without filtering. Under `--display ndjson`, stderr emits one JSON-RPC notification per line for wrapper consumption.

The TypeScript and Python wrapper SDKs ([`wrappers/typescript/`](wrappers/typescript/), [`wrappers/python-py/`](wrappers/python-py/)) handle all of this: they spawn `amplifier-agent run`, parse the envelope and the ndjson stream, and expose a typed async API.

## Related repositories

- [`microsoft/amplifier`](https://github.com/microsoft/amplifier) — Top-level Amplifier project
- [`microsoft/amplifier-core`](https://github.com/microsoft/amplifier-core) — The kernel
- [`microsoft/amplifier-foundation`](https://github.com/microsoft/amplifier-foundation) — Bundle + module system
- [`microsoft/amplifier-app-cli`](https://github.com/microsoft/amplifier-app-cli) — Interactive REPL CLI for end users
- [`microsoft/amplifier-app-openclaw`](https://github.com/microsoft/amplifier-app-openclaw) — OpenClaw integration
- [`microsoft/amplifier-agent`](https://github.com/microsoft/amplifier-agent) — this repo

## Status

Current versions: engine `0.8.0`, TypeScript wrapper `amplifier-agent-ts@0.7.0`, Python wrapper `amplifier-agent-py@0.3.0` (see [`wrappers/python-py/`](wrappers/python-py/)). Wire protocol: `0.3.0`.

**Shipped:**

- Engine library + CLI (per-turn subprocess model)
- TypeScript + Python wrapper SDKs (`amplifier-agent-ts` on npm; `amplifier-agent-py` BYO-engine)
- Protocol schemas + cross-language conformance test suite ([`wrappers/conformance/`](wrappers/conformance/))
- User-invoked storage migrations (`amplifier-agent migrate`)
- Workspace-scoped session storage

**In progress / next:**

- Host adapters (NanoClaw, Paperclip) — see [`docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md`](docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md)
- Container packaging, install-path finalization

See [`docs/designs/`](docs/designs/) and the [pull requests](https://github.com/microsoft/amplifier-agent/pulls) for design history and roadmap.

## Contributing

This project follows the Microsoft Open Source [Code of Conduct](CODE_OF_CONDUCT.md). For per-repo conventions (build/test commands, cross-component invariants, commit scopes), see [`AGENTS.md`](AGENTS.md).

- Issues and PRs welcome.
- For security disclosures, see [`SECURITY.md`](SECURITY.md).
- For support guidance, see [`SUPPORT.md`](SUPPORT.md).

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.

## License

MIT — see [`LICENSE`](LICENSE).

---

🤖 Built with [Amplifier](https://github.com/microsoft/amplifier).
