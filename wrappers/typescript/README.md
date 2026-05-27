# amplifier-agent-ts

TypeScript SDK for the [Amplifier agent](https://github.com/microsoft/amplifier-agent). Spawns and drives the `amplifier-agent` Python CLI over a stdio protocol.

> **Two packages, related but distinct.** This is the npm package, named `amplifier-agent-ts`. The Python engine it spawns is a separate package on PyPI named `amplifier-agent`. You need **both** for the SDK to work:
> - `npm install amplifier-agent-ts` — this package, the TypeScript SDK
> - `uv tool install amplifier-agent` (or `pip install amplifier-agent`) — the Python engine

## What this is

```
┌─────────────────────────────────────────┐
│ Your Node.js application                │
│   import { spawnAgent }                 │
│     from 'amplifier-agent-ts'           │
└─────────────────┬───────────────────────┘
                  │ child_process.spawn
                  │ + NDJSON over stdio
                  ▼
┌─────────────────────────────────────────┐
│ amplifier-agent (Python CLI)            │
│   - All AI inference                    │
│   - All tool execution                  │
│   - All session state                   │
└─────────────────────────────────────────┘
```

This SDK is a thin process supervisor. It resolves the engine binary, builds a subprocess environment, validates parameters, and exposes a `SessionHandle` whose `submit()` method launches the engine per turn (Mode A). All inference, tool execution, and session state live in the Python engine — not here.

## Install

```bash
npm install amplifier-agent-ts
```

This SDK has **zero npm runtime dependencies**. You also need the Python engine on your system — see [Runtime requirements](#runtime-requirements).

## Quick start

```typescript
import { spawnAgent, AaaError } from 'amplifier-agent-ts';
import { randomUUID } from 'node:crypto';

const session = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: randomUUID(),
});

try {
  // The engine is launched per submit() (Mode A v2).
  // See SessionHandle in dist/index.d.ts for the full submit() signature.
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

For the full public API, see the type definitions in `dist/index.d.ts` and the source at [`src/index.ts`](https://github.com/microsoft/amplifier-agent/blob/main/wrappers/typescript/src/index.ts).

## Runtime requirements

| Requirement | Why |
|---|---|
| Node.js ≥20 | The SDK uses `node:child_process` and `node:readline` |
| `amplifier-agent` Python CLI on PATH | The SDK spawns this binary on every `submit()` |
| Network access to `github.com` | The engine resolves agent modules from `git+https://` URLs at first invocation and per-run for the active provider |
| `@types/node` (TypeScript consumers only) | Not declared as a peer dependency to avoid install warnings, but required to compile against this package's `.d.ts`. Most TypeScript projects already have it. |

### Installing the Python engine

```bash
# Recommended
uv tool install amplifier-agent

# Or pip
pip install amplifier-agent
```

The engine is a Rust/Python hybrid built with PyO3 + maturin. On standard platforms (Linux x86_64, Linux aarch64, macOS arm64, Windows x64), pre-built wheels are downloaded automatically from PyPI — **no Rust toolchain required**. On platforms without a matching wheel (Alpine/musl, FreeBSD, exotic architectures), installation falls back to source and requires `rustc 1.70+` and `maturin`.

The SDK looks for the binary via `which amplifier-agent`, or you can set `AMPLIFIER_AGENT_BIN` to an absolute path.

### Network access

The Amplifier engine resolves agent modules from git URLs in the bundle manifest. On first invocation it clones and installs ~11 modules; on every invocation it injects the active provider module (anthropic / openai / azure-openai / ollama) from a `git+https://` URL. **Fully air-gapped environments are not supported today.** Network access to `github.com` is required at least on first run and remains required for provider module resolution on subsequent runs.

## Version coupling

This SDK speaks **Amplifier protocol version 0.1.0**, exported as `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` and forwarded to the engine via `--protocol-version` on every `submit()`. The engine rejects protocol mismatches unless you opt out with `allowProtocolSkew: true`.

The SDK is also coupled to the engine's argv surface and stdout envelope schema. **Treat the SDK and engine as a coupled pair**: a major engine update may require a major SDK update. Pin both in your application.

## License

MIT. See `LICENSE`.

## Repository, issues, contributing

- Repository: https://github.com/microsoft/amplifier-agent (monorepo; SDK source is in `wrappers/typescript/`)
- Issues: https://github.com/microsoft/amplifier-agent/issues
