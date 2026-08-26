# Integration guide

How to build software on `amplifier-agent`.

**The library is the product.** `amplifier_agent_lib` is the engine; every other surface in this repo is a convenience wrapper over it. The CLI is an argv and stdio adapter, the HTTP face is an OpenAI-compatible adapter, and the TypeScript and Python SDKs are subprocess clients for hosts that cannot import Python in-process. The contract is the edge of the library, and the wrappers exist to reach it from places that cannot.

Start with the library. Reach for a wrapper when your host genuinely cannot embed it.

The engine runs **one turn per invocation** and exits. Continuity across turns comes from a session ID, not from a long-lived process. That holds for the library too: an embedded `Engine` boots, takes a turn, and shuts down, and the next turn resumes by session ID.

## Before you start

`amplifier-agent` is self-contained. You do not need the Amplifier CLI, bundles, or any other repository in the `microsoft/amplifier*` family, and none of them is a substitute for it here.

Use it when your software needs to run an agent: a loop with tools, file access, sub-agents, and/or multi-turn state. It also works for plain LLM calls, where you get routing across nine providers behind one interface.

## Install

One distribution ships the library and the `amplifier-agent` binary together. Installing either gets you both.

Git is the supported and tested channel. PyPI artifacts are published on every tag but nothing in this repo exercises that path, so treat it as unverified.

```bash
uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent"
```

which records in your `pyproject.toml`:

```toml
dependencies = ["amplifier-agent"]

[tool.uv.sources]
amplifier-agent = { git = "https://github.com/microsoft/amplifier-agent" }
```

Or into an existing environment:

```bash
uv pip install "git+https://github.com/microsoft/amplifier-agent"
```

If your host does not embed the library and only spawns the binary, the installer script is the lighter path. See [INSTALL.md](INSTALL.md). Install as the same user that runs your host process, since a host spawning a subprocess inherits that user's `PATH`.

Either way, the binary doubles as your setup and diagnostics surface. Use it to verify the environment before writing integration code, whether or not it is on your runtime path:

```bash
amplifier-agent doctor           # env, providers, paths, bundle cache
amplifier-agent auth set anthropic sk-ant-...
amplifier-agent models list      # provider-namespaced model IDs
amplifier-agent version          # engine and wire protocol versions
```

Credentials resolve from the environment first, then `~/.amplifier-agent/credentials.json`. See [CONFIGURATION.md](CONFIGURATION.md).

## Embedding the library

The engine imports and runs in your process. No subprocess, no wire protocol, no envelope parsing, and the display and approval systems are your own objects rather than a stream you have to parse.

This is a complete, working turn:

```python
import asyncio
import sys

from amplifier_agent_cli.provider_sources import inject_provider, inject_routing_matrix
from amplifier_agent_lib import __version__
from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.engine import Engine
from amplifier_agent_lib.protocol import PROTOCOL_VERSION, server_default_capabilities
from amplifier_agent_lib.protocol_points.defaults_cli import (
    CliApprovalSystem,
    CliDisplaySystem,
)


async def main() -> None:
    prepared = await load_and_prepare_cached(aaa_version=__version__)

    # Clear the catalog stubs the bundle declares, then inject the provider you
    # want. inject_provider is a no-op if any provider is already mounted, so
    # skipping the clear silently discards your injection.
    prepared.mount_plan["providers"] = []
    inject_provider(prepared, "anthropic")
    inject_routing_matrix(prepared, "anthropic")

    handler = make_turn_handler(
        prepared,
        cwd="/path/to/agent/workdir",
        is_resumed=False,
        workspace="my-app",
    )

    engine = Engine(
        turn_handler=handler,
        protocol_points={
            "approval": CliApprovalSystem(mode="yes"),
            "display": CliDisplaySystem(stream=sys.stderr, verbosity="quiet"),
        },
    )

    await engine.boot(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "my-app", "version": "1.0.0"},
            "capabilities": dict(server_default_capabilities()),
            "sessionId": "chat-42",
            "resume": False,
            "cwd": "/path/to/agent/workdir",
        },
        bundle_override=prepared,
    )

    try:
        result = await engine.submit_turn(
            {"sessionId": "chat-42", "turnId": "turn-1", "prompt": "Hello, agent."}
        )
        print(result["reply"])
    finally:
        await engine.shutdown()


asyncio.run(main())
```

`submit_turn` returns eight keys:

```python
{
  "reply": "...", "turnId": "turn-1", "sessionId": "chat-42",
  "tokensIn": 12776, "tokensOut": 4,
  "cacheReadTokens": 11904, "cacheWriteTokens": 0,
  "costUsd": Decimal("0.0062472"),
}
```

Usage comes off the return value directly. You do not need the CLI envelope to account for tokens or cost. `costUsd` is a `Decimal`, so `json.dumps(result)` raises `TypeError` unless you pass `default=str`. It is `None` when the provider reported no cost, which is not the same as zero. The numbers depend on prompt-cache state, so the same prompt twice will not report the same split.

### Choosing a provider

Discover which providers have resolvable credentials on this machine, then pick one deliberately:

```python
from amplifier_agent_cli.provider_sources import enumerate_resolvable_providers

available = enumerate_resolvable_providers()   # e.g. ['anthropic', 'openai', 'gemini']
```

Pick from that list against your own preference order rather than taking the first entry, since credential resolution says only that a key was found. To carry host configuration into the provider (`default_model`, `effort`, and similar), pass `provider_config_from_host(host_config)` as `inject_provider(..., extra_config=...)`.

### Multi-turn and continuity

Continuity comes from the session ID and the persisted transcript, not from keeping an `Engine` alive. Calling `submit_turn` twice on one booted `Engine` succeeds, but the second turn does **not** see the first: each turn builds its own context from the transcript on disk.

Build one `Engine` per turn and pass `is_resumed=True` for every turn after the first, reusing the same `sessionId` and `workspace`:

```python
handler = make_turn_handler(prepared, cwd=..., is_resumed=True, workspace="my-app")
```

Session state lives under `$AMPLIFIER_AGENT_HOME/state/workspaces/<workspace>/sessions/<session-id>/`, and continuity is per `(workspace, session-id)` pair. Set `workspace` explicitly. Without it, sessions are scoped to the working directory, so a host that runs from varying directories sees its sessions fragment.

### What to know before you ship

- **Everything is async.** `load_and_prepare_cached`, `boot`, `submit_turn`, and `shutdown` are all coroutines. There is no sync facade.
- **`import amplifier_agent_lib` sets `AMPLIFIER_HOME`** in `os.environ`, unconditionally, at import time, overwriting any value you already set. If your host also uses `amplifier-foundation` or reads that variable, import order matters to you.
- **The first turn is slow.** A cold bundle cache is prepared on first use. A `prepared.pickle is corrupted (ModuleNotFoundError); rebuilding` warning on first run from a new environment is benign and self-healing; the cache is keyed by engine version and bundle digest, not by interpreter.
- **`CliApprovalSystem()` with no arguments declines everything.** Auto-approve is `mode="yes"`.
- **The packages ship no `py.typed`**, so type checkers treat the imported symbols as untyped.

Normative contract: [`spec/engine-api.md`](spec/engine-api.md). Layer boundaries: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## When you cannot embed

| Your host | Use | Section |
|---|---|---|
| Python, in-process | `amplifier_agent_lib` | [Embedding the library](#embedding-the-library) |
| Node.js or TypeScript | `amplifier-agent-ts` npm package | [TypeScript SDK](#typescript-sdk) |
| Python, needs process isolation | `amplifier-agent-py` wrapper | [Python SDK](#python-sdk) |
| Already speaks OpenAI chat completions | `amplifier-agent serve chat-completions` | [HTTP face](#http-face) |
| A shell script, or a language with no SDK | The CLI contract | [Wire protocol](#wire-protocol) |

All of these sit on the same engine, and reach it by spawning the binary or calling the server rather than importing the library.

The SDKs are **BYO-engine**: zero runtime dependencies, and they locate the `amplifier-agent` binary on `PATH` (or at `AMPLIFIER_AGENT_BIN`).

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

A subprocess client for Python hosts that want process isolation between host and engine. If you do not need that isolation, [embed the library](#embedding-the-library) instead and skip the process boundary.

```bash
uv add "amplifier-agent-py @ git+https://github.com/microsoft/amplifier-agent#subdirectory=wrappers/python-py"
```

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

An async variant (`spawn_agent`, returning `SessionHandle`) is also exported. Parameters mirror the TypeScript SDK one for one, and that symmetry is enforced by a conformance suite. Runnable examples: [`wrappers/python-py/examples/`](../wrappers/python-py/examples/) contains `sync_chat.py`, `async_chat.py`, and `diagnostic.py`.

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

This section describes the **subprocess boundary**. None of it applies to an embedder: there is no argv, no envelope, and no exit code when you import the library.

Protocol version **`0.4.0`**, defined in `src/amplifier_agent_lib/protocol/methods.py`. Breaking changes bump it. Wrappers must pass `--protocol-version 0.4.0`; a mismatch returns `protocol_version_mismatch` and exits non-zero rather than silently misbehaving.

The wrapper passes flags as argv. The engine writes one JSON envelope line to stdout on completion.

**Input, selected argv flags:**

| Flag | Type | Purpose |
|---|---|---|
| `PROMPT` | positional | The turn prompt |
| `--prompt-file` | path | Read the prompt from a UTF-8 file instead of the positional argument. Mutually exclusive with `PROMPT`; wrappers use it automatically for large prompts |
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
  "protocolVersion": "0.4.0",
  "sessionId": "...",
  "turnId": "turn-1",
  "reply": "...",
  "error": null,
  "metadata": {
    "tokensIn": 0, "tokensOut": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0,
    "costUsd": null, "durationMs": 0,
    "bundleDigest": "...", "engineVersion": "...",
    "protocolVersion": "0.4.0", "correlationId": "...",
    "activeMode": null
  }
}
```

The usage fields are the same numbers `submit_turn` returns to an embedder; the CLI does no arithmetic of its own. `activeMode` echoes the `--mode` value for the turn, `null` when omitted. Under `--output text` (the default) stdout is the reply text only, which is easier to pipe into shell tooling.

**Streams are strictly separated.** Diagnostic events (tool calls, thinking, progress) go to **stderr** only. Stdout carries the envelope or reply so callers can parse it without filtering. Under `--display ndjson`, stderr emits one JSON-RPC notification per line for wrapper consumption.

Normative contracts: [`spec/wire-protocol.md`](spec/wire-protocol.md), [`spec/envelope-and-errors.md`](spec/envelope-and-errors.md), [`spec/wrapper-contract.md`](spec/wrapper-contract.md).

## Session continuity

Sessions persist as transcript JSONL under `$AMPLIFIER_AGENT_HOME/state/workspaces/<workspace>/sessions/<session-id>/`. Continuity is per `(workspace, session-id)` pair, on every surface.

Embedders control this with `is_resumed` on `make_turn_handler` and `resume` in the boot params. Subprocess callers use flags:

```bash
amplifier-agent run -y --session-id chat-42 "My favorite color is blue."
amplifier-agent run -y --session-id chat-42 --resume "What did I say my favorite color was?"
amplifier-agent run -y --session-id chat-42 --fresh "Start over."
```

`--resume` and `--fresh` are mutually exclusive. Passing both exits with `Error: --resume and --fresh are mutually exclusive`.

Pass `--workspace <name>` to isolate session state per project. Without it, sessions are scoped to the current working directory, which means a host that spawns from varying directories will see its sessions fragment. Multi-tenant hosts should always set `--workspace` explicitly.

## Approval policy

An embedder supplies an `ApprovalSystem` object directly, so the policy is whatever that object does. `CliApprovalSystem(mode="yes")` auto-approves, `mode="no"` auto-declines, and the no-argument default declines. Implement the protocol yourself for a real human-in-the-loop channel; it must honor `timeoutMs` and return `{"action": "cancel"}` on timeout.

A host that **spawns** the engine headlessly must declare an approval policy on argv or in a config file, or the run refuses to start: `-y`, `-n`, or `approval.mode`. With none of those and no TTY, the run exits 2 with `approval_unconfigured` rather than auto-denying every tool call and still exiting 0, which looked like success while doing no work.

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

An embedder can load the same file with `amplifier_agent_lib.config.load_config` and pass the result to `make_turn_handler(host_config=...)`, or skip the file entirely and construct the dict in code.

## Checklist for embedding

1. Declare `amplifier-agent` as a dependency, and confirm the environment with `amplifier-agent doctor`.
2. Choose a provider from `enumerate_resolvable_providers()` against your own preference order, and fail loudly when the list is empty.
3. Clear `prepared.mount_plan["providers"]` before `inject_provider`, or your injection is discarded.
4. Set `workspace` so session state does not follow the working directory.
5. Build one `Engine` per turn and pass `is_resumed=True` after the first, reusing the session ID.
6. Supply an `ApprovalSystem` that matches your trust model. The default declines.
7. Record `__version__` and the usage fields from every turn alongside your own logs.

## Checklist for a subprocess integration

1. Install the engine as the user that runs your host, and confirm with `amplifier-agent doctor`.
2. Pin `--protocol-version` to the version your SDK targets. Fail loudly on mismatch.
3. Declare an approval policy explicitly, and never rely on the TTY fallback in a service. On the HTTP face the policy is inert and every tool call is auto-approved, so isolate that server instead.
4. Set `--workspace` so session state does not follow the working directory.
5. Pass `--output json --display ndjson` and parse both streams. Never parse stderr for results.
6. Write one host config file per agent instance.
7. Record `metadata.engineVersion` and `metadata.bundleDigest` from the envelope alongside your own logs.

## Reference

- Library contract: [`spec/engine-api.md`](spec/engine-api.md)
- Architecture and layer boundaries: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Normative specifications index: [`SPEC.md`](SPEC.md)
- Configuration: [`CONFIGURATION.md`](CONFIGURATION.md)
- Command and flag reference: [`CLI.md`](CLI.md)
- Applications already integrated: [`ECOSYSTEM.md`](ECOSYSTEM.md)
