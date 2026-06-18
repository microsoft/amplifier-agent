# Advanced

Beyond the basic spawn-and-iterate pattern: approval handling, MCP servers, env allowlist, custom binary paths, listing models, manual subprocess control.

## Environment allowlist

The SDK passes a **filtered** environment to the engine subprocess. Only these are forwarded:

1. Keys with `AMPLIFIER_` prefix (always).
2. Keys with `LC_` prefix (always).
3. Keys you list in `env.allowlist`.
4. Keys you set in `env.extra` (last writer wins, but must not be in `BLOCKED_ENV_KEYS`).

### `DEFAULT_ALLOWLIST`

```ts
const DEFAULT_ALLOWLIST = ['PATH', 'HOME', 'USER', 'LANG', 'TERM', 'TMPDIR'];
```

`PATH`, `HOME`, and `USER` are essential. The others are widely-needed but not required.

### Provider keys are NOT in the default allowlist

`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `OLLAMA_HOST` — none of these are `AMPLIFIER_*` prefixed, so they are **not** passed through unless you add them:

```ts
await spawnAgent({
  // ...
  env: {
    allowlist: [
      ...DEFAULT_ALLOWLIST,
      'ANTHROPIC_API_KEY',     // forward the host's anthropic key
      'OPENAI_API_KEY',        // ...and openai
    ],
  },
});
```

Or pass them via `extra` if you want to provide them explicitly:

```ts
await spawnAgent({
  // ...
  env: {
    allowlist: DEFAULT_ALLOWLIST,
    extra: {
      ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY!,
    },
  },
});
```

### Blocked keys

`BLOCKED_ENV_KEYS` cannot be set via `env.extra` — attempting to do so throws `AaaError('env_injection_rejected')`:

```ts
const BLOCKED_ENV_KEYS = new Set([
  'PYTHONPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONSTARTUP',
  'PATH', 'PYTHONHOME', 'PYTHONNOUSERSITE',
  'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH',
]);
```

These are dynamic-linker hooks that could be used for code injection into the engine subprocess. The SDK refuses to pass them through `extra`. (`PATH` is blocked from `extra` only — it's a normal allowlist key and is forwarded from the caller's env by default.)

If you really need to set one — and you almost never do — the workaround is to set it in the parent process's env before spawn, *and* add it to `allowlist`. This requires both an explicit grant from the caller and from the wrapper, and that's the point.

---

## Custom binary paths

The SDK resolves the engine binary in this order:

1. `AMPLIFIER_AGENT_BIN` env var (if set, used verbatim — even if the path doesn't exist on disk, so the error is descriptive).
2. `which amplifier-agent` via the shell.
3. Throws `Error('binary_not_found')`.

### Override per-call

```ts
import { resolveBinaryPath, spawnAgent } from 'amplifier-agent-ts';

const customBin = '/opt/my-amplifier-agent/bin/amplifier-agent';

const handle = await spawnAgent({
  // ...
  env: {
    allowlist: DEFAULT_ALLOWLIST,
    extra: { AMPLIFIER_AGENT_BIN: customBin },
  },
});
```

### Override globally

```bash
export AMPLIFIER_AGENT_BIN=/path/to/custom/amplifier-agent
```

Then all SDK calls in that process pick it up via the default `process.env`.

### Test/sandbox injection

For tests, you can replace the resolver and the version probe directly:

```ts
await spawnAgent({
  // ...
  _binaryResolver: () => '/fake/bin/amplifier-agent',
  _engineVersionProbe: async () => ({ version: '0.5.2', protocolVersion: '0.3.0' }),
});
```

These leading-underscore parameters are part of the public type but are documented as test-only.

---

## Approval

The `approval.mode` field controls how the engine handles tool calls.

```ts
type ApprovalMode = 'yes' | 'no' | 'prompt';

await spawnAgent({
  // ...
  approval: { mode: 'yes' },
});
```

| Mode | Engine flag | Behavior |
|---|---|---|
| `'yes'` | `-y` | Auto-approve every tool call. |
| `'no'` | `-n` | Auto-deny every tool call. |
| `'prompt'` | (none) | Defer to engine's policy resolution: host_config.approval.mode → TTY check → fail. |
| `undefined` | `-y` | Historical default for backward compat. Will change. **Set this explicitly.** |

### Mid-turn approval prompts (not yet supported)

The protocol reserves space for a future approval channel where the engine pauses and asks the host to approve each tool call interactively. The SDK type accepts `approval.onRequest`, but **currently rejects** it at spawn time:

```ts
await spawnAgent({
  approval: { mode: 'prompt', onRequest: handler },
});
// → throws AaaError; mid-turn channel is not implemented in v1
```

The `makeApprovalHandler()` and `ApprovalAdapter`/`ApprovalRequest`/`ApprovalResponse` types exist for forward compatibility. Until the channel ships, use `'yes'`, `'no'`, or `'prompt'` (the latter relying on `host_config.approval.mode`).

---

## MCP servers

You can configure [MCP](https://modelcontextprotocol.io/) servers per-call by passing `mcpServers`:

```ts
await spawnAgent({
  // ...
  mcpServers: {
    'my-server': {
      command: 'node',
      args: ['/path/to/server.js'],
      env: { /* server-side env */ },
    },
    'other-server': { command: 'python', args: ['-m', 'my_mcp_server'] },
  },
});
```

The SDK:

1. Writes the configuration as JSON to a `0600` tempfile.
2. Sets `AMPLIFIER_MCP_CONFIG=<path>` in the subprocess env.
3. Unlinks the tempfile when you call `cancel()` or when the subprocess exits.

If you prefer to manage the file yourself, set `AMPLIFIER_MCP_CONFIG` directly via `env.extra` (and skip `mcpServers`).

### `resolveMcpConfigPath` / `cleanupSpillFile`

These helpers let you do the spill yourself:

```ts
import { resolveMcpConfigPath, cleanupSpillFile } from 'amplifier-agent-ts';

const { path, cleanup } = await resolveMcpConfigPath({
  servers: { 'my-server': { command: 'node', args: ['server.js'] } },
});

try {
  // Use `path` as you wish, e.g. set AMPLIFIER_MCP_CONFIG yourself.
} finally {
  await cleanup();         // or cleanupSpillFile(path)
}
```

---

## Listing models

```ts
import { listModels } from 'amplifier-agent-ts';

// One provider, full catalog
const result = await listModels({ provider: 'anthropic', timeoutMs: 15000 });
console.log(result.provider, '→', result.models.length, 'models');
console.log(result.models.map(m => m.id));

// One provider, latest per family only
const latest = await listModels({ provider: 'anthropic', latest: true });

// All providers in parallel (aggregate envelope)
const all = await listModels({ timeoutMs: 30000 });
for (const entry of all.results) {
  console.log(entry.provider, entry.status, entry.models?.length ?? 0);
}
```

Same shape as `amplifier-agent models list --output json` — see [CLI: models list](../user/cli-reference.md#models-list).

Throws `ListModelsError` if the underlying CLI call fails (provider down, binary missing, malformed JSON).

---

## Protocol version skew

`spawnAgent` aborts with `AaaError('protocol_version_mismatch')` if the engine reports a wire protocol version different from `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` (currently `"0.3.0"`).

Override in dev:

```ts
await spawnAgent({
  // ...
  allowProtocolSkew: true,
});
```

Or via env (read by the engine boot path itself):

```bash
export AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1
```

(or put `"allowProtocolSkew": true` in your host config).

Don't ship this in production. Mismatch means the wrapper and engine may disagree on event shapes, error codes, or argv flags.

---

## Manual subprocess control

If you want to bypass `spawnAgent` and `SessionHandle`, the building blocks are public:

```ts
import {
  assembleArgv,
  resolveBinaryPath,
  buildEnv,
  probeEngineVersion,
  checkProtocolVersion,
  parseRunOutput,
  parseNdjsonStream,
  PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  DEFAULT_ALLOWLIST,
} from 'amplifier-agent-ts';
import { spawn } from 'child_process';

const bin = resolveBinaryPath();
const env = buildEnv({
  processEnv: process.env,
  allowlist: [...DEFAULT_ALLOWLIST, 'ANTHROPIC_API_KEY'],
});

// Probe & check
const probed = await probeEngineVersion(bin, env);
const check = checkProtocolVersion({
  wrapper: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  engine: probed.protocolVersion,
  allowSkew: false,
});
if (!check.ok) {
  throw new Error(check.remediation);
}

// Build argv
const argv = assembleArgv({
  sessionId: 'manual-1',
  resume: false,
  protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  displayMode: 'ndjson',
  approvalMode: 'yes',
  prompt: 'Hello',
});

// Spawn
const child = spawn(bin, argv, { env, stdio: ['ignore', 'pipe', 'pipe'] });

// Consume NDJSON from stderr
for await (const note of parseNdjsonStream(child.stderr, {
  onParseError: (raw, e) => console.warn('drop', raw),
})) {
  console.log('wire:', note);
}

// Wait for exit and parse stdout
let stdout = '';
child.stdout.on('data', (d) => { stdout += d; });
const exitCode = await new Promise<number>((res) =>
  child.on('exit', (c) => res(c ?? 0)),
);

// Build a DisplayEvent from the outcome
const ev = parseRunOutput({
  stdout, stderr: '', exitCode, signal: null,
});
console.log('Final:', ev);
```

This is what `SessionHandle.submit()` does internally. The public helpers exist so hosts with unusual requirements (sandboxed spawns, custom logging, alternative event pipelines) can compose their own driver while reusing the SDK's argv discipline and parsing.

---

## Timeouts

`timeoutMs` caps a single `submit()` call's wall-clock duration.

```ts
await spawnAgent({
  // ...
  timeoutMs: 60_000,    // 60s
});
```

| Value | Behavior |
|---|---|
| `undefined` or `0` | No timeout. (The CLI itself has no per-turn cap.) |
| `DEFAULT_TIMEOUT_MS` (600_000 / 10 min) | The conservative recommended cap for interactive UIs. |
| `N > 0` | After `N` ms, the SDK calls `cancel()` and the `submit()` iterator emits an `error` event with `code: 'timeout'` (or similar) before ending. |

The CLI itself does not enforce a timeout. The SDK enforces it by SIGTERM-ing the subprocess.
