# TypeScript SDK API reference

Every public export from `amplifier-agent-ts`. Types are paraphrased — see the package's `.d.ts` for canonical signatures.

## Entry point

```ts
import {
  // Main API
  spawnAgent, SessionHandle, AaaError,
  // Constants
  PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  DEFAULT_TIMEOUT_MS,
  STDERR_TAIL_BYTES,
  DEFAULT_ALLOWLIST,
  BLOCKED_ENV_KEYS,
  // Helpers
  assembleArgv,
  resolveBinaryPath,
  buildEnv,
  probeEngineVersion,
  checkProtocolVersion,
  parseRunOutput,
  parseNdjsonStream,
  Transport,
  makeApprovalHandler,
  listModels, ListModelsError,
  resolveMcpConfigPath, cleanupSpillFile,
} from 'amplifier-agent-ts';
```

## Constants

| Constant | Value | Source |
|---|---|---|
| `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` | `"0.3.0"` | `index.ts` |
| `DEFAULT_TIMEOUT_MS` | `600000` (10 min) | `session.ts` |
| `STDERR_TAIL_BYTES` | `4096` | `run-output-parser.ts` |
| `DEFAULT_ALLOWLIST` | `['PATH', 'HOME', 'USER', 'LANG', 'TERM', 'TMPDIR']` | `spawn.ts` |
| `BLOCKED_ENV_KEYS` | `Set` of `['PYTHONPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONSTARTUP', 'PATH', 'PYTHONHOME', 'PYTHONNOUSERSITE', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH']` | `spawn.ts` |

---

## `spawnAgent(params): Promise<SessionHandle>`

Validates parameters, resolves the engine binary, probes its version, builds the subprocess env, and constructs a `SessionHandle`. **No subprocess is spawned.**

```ts
interface SpawnAgentParams {
  lifecycle: 'one-shot';            // only value supported
  sessionId: string;                // required
  resume: boolean;                  // required — true = --resume, false = --fresh
  cwd?: string;
  configPath?: string;              // forwarded as --config <path>
  approval?: {
    mode?: 'yes' | 'no' | 'prompt';
    // (other fields reserved for future approval-channel work)
  };
  displayMode?: 'text' | 'ndjson';  // set to 'ndjson' to receive 'notification' events
  workspace?: string;
  mcpServers?: Record<string, McpServerConfig>;  // see Advanced
  timeoutMs?: number;               // wall-clock cap; 0/undefined = no timeout
  allowProtocolSkew?: boolean;
  env: {
    allowlist: string[];            // required
    extra?: Record<string, string>;
  };
  // Test/sandbox injection points (advanced):
  runChildProcess?: ChildProcessFactory;
  _binaryResolver?: () => string;
  _engineVersionProbe?: (bin: string, env: Record<string, string>) => Promise<EngineVersionPayload>;
}
```

### Behavior

1. Reject `approval.onRequest` (mid-turn callbacks not supported in v1).
2. Validate `lifecycle === 'one-shot'`.
3. Resolve the binary: `_binaryResolver` → `resolveBinaryPath()`.
4. Build the subprocess env via `buildEnv()`.
5. Probe the engine: `probeEngineVersion(binary, env)`.
6. `checkProtocolVersion({ wrapper: '0.3.0', engine: <probed>, allowSkew })`. Throws `AaaError('protocol_version_mismatch', ...)` on fail unless `allowProtocolSkew: true`.
7. Construct and return a `SessionHandle`.

### Throws

- `AaaError('protocol_version_mismatch', ...)` — engine and wrapper disagree on protocol version.
- `AaaError('env_injection_rejected', ...)` — caller passed a blocked env key in `env.extra`.
- `AaaError('binary_not_found', ...)` — no engine on PATH and no `AMPLIFIER_AGENT_BIN`.
- Any error thrown by `probeEngineVersion()` if the engine is broken.

---

## `SessionHandle`

```ts
class SessionHandle {
  submit(prompt: string): AsyncIterable<DisplayEvent>;
  cancel(): Promise<void>;
  dispose(): Promise<void>;                 // alias for cancel()
  getEngineInfo(): EngineInfo;
}

interface EngineInfo {
  binaryPath: string;
  protocolVersion: string;
  engineVersion: string;
  bundleDigest: string;
}
```

### `submit(prompt)`

Spawns the engine subprocess and yields events. **One-shot** — calling `submit()` a second time on the same handle throws.

Yields events in this rough order:

1. `init` — yielded **before** the subprocess spawn so callers can see the resolved session ID.
2. `notification` events from the engine's stderr (only when `displayMode: 'ndjson'`).
3. `activity` events every ~2 seconds while the engine is alive.
4. Either a single `result` event (success) or a single `error` event (failure).

The iterator ends when the subprocess exits.

### `cancel()`

Sends `SIGTERM` to the engine's process group. Five seconds later, sends `SIGKILL` if still alive. Removes any temp files written for `mcpServers` (see Advanced).

`dispose()` is an alias.

### `getEngineInfo()`

Returns the metadata captured during `spawnAgent()`. `bundleDigest` is reserved and currently empty.

---

## `DisplayEvent`

A discriminated union yielded by `submit()`:

```ts
type DisplayEvent =
  | { type: 'init'; sessionId: string }
  | { type: 'activity' }
  | { type: 'result'; text: string }
  | { type: 'error';
      code: string;
      classification: 'engine' | 'protocol' | 'approval' | 'transport' | 'unknown';
      severity: 'error';
      correlationId: string;
      message: string;
      stderrTail?: string;
      retryable?: boolean;
    }
  | { type: 'notification'; method: string; params: unknown };
```

See [Events](events.md) for examples of every event type.

---

## `AaaError`

```ts
class AaaError extends Error {
  code: string;
  classification: 'engine' | 'protocol' | 'approval' | 'transport' | 'unknown';
  severity: 'error';
  correlationId?: string;
  remediation?: string;
  stderrTail?: string;

  constructor(
    code: string,
    message: string,
    opts?: { classification, severity, correlationId?, remediation?, stderrTail? }
  );
}
```

Thrown from synchronous validation paths (`spawnAgent`, `buildEnv`, etc.). For runtime errors during a `submit()`, you receive an `error` `DisplayEvent` instead.

---

## Helper functions

These are the lower-level building blocks. Use them if you want to drive the engine yourself or test in isolation.

### `assembleArgv(input): string[]`

Build the canonical argv vector.

```ts
interface AssembleArgvInput {
  sessionId: string;
  resume: boolean;                   // true → --resume, false → --fresh
  cwd?: string;
  configPath?: string;
  protocolVersion: string;
  displayMode?: 'text' | 'ndjson';
  workspace?: string;
  approvalMode?: 'yes' | 'no' | 'prompt';  // 'prompt' emits neither -y nor -n; undefined defaults to 'yes'
  prompt: string;
}
```

Example:

```ts
assembleArgv({
  sessionId: 's1', resume: false, protocolVersion: '0.3.0',
  approvalMode: 'yes', prompt: 'hi',
})
// → [
//   'run', '--session-id', 's1', '--fresh',
//   '--output', 'json', '--protocol-version', '0.3.0',
//   '-y', 'hi'
// ]
```

The canonical order is: `run`, `--session-id`, `<id>`, `--resume|--fresh`, `[--cwd]`, `[--config]`, `--output json`, `--protocol-version`, `<ver>`, `[--display]`, `[--workspace]`, `[-y|-n]`, `<prompt>`.

### `resolveBinaryPath(opts?): string`

```ts
interface ResolveBinaryPathOptions {
  env?: Record<string, string | undefined>;  // defaults to process.env
}
```

Resolution order:

1. `env.AMPLIFIER_AGENT_BIN` if set (returned even if path doesn't exist on disk, so caller can produce a useful error).
2. `which amplifier-agent` via shell.
3. Throws `Error` with `code: 'binary_not_found'`.

### `buildEnv(opts): Record<string, string>`

```ts
interface BuildEnvOptions {
  processEnv: Record<string, string | undefined>;  // typically process.env
  allowlist: string[];                              // exact-match keys to pass through
  extra?: Record<string, string>;                   // merged last; throws on BLOCKED_ENV_KEYS
}
```

Passes through: keys in `allowlist`, keys with `AMPLIFIER_` prefix, keys with `LC_` prefix. `extra` is merged last (overrides allowlisted values). Throws `AaaError('env_injection_rejected')` if `extra` contains any key in `BLOCKED_ENV_KEYS`.

### `probeEngineVersion(bin, env, timeoutMs?): Promise<EngineVersionPayload>`

```ts
interface EngineVersionPayload {
  version: string;
  protocolVersion: string;
}
```

Spawns `amplifier-agent version --json` with a 5-second default timeout. Returns the parsed payload or throws.

### `checkProtocolVersion(opts): VersionCheckResult`

```ts
interface CheckProtocolVersionOptions {
  wrapper: string;          // e.g. PROTOCOL_VERSION_REQUIRED_BY_WRAPPER
  engine: string;
  allowSkew: boolean;
}

type VersionCheckResult =
  | { ok: true }
  | { ok: false; code: 'protocol_version_mismatch'; remediation: string };
```

When `allowSkew` is true, always returns `{ ok: true }`. When false, returns `{ ok: false, ... }` if the strings don't match exactly.

### `parseRunOutput(outcome): DisplayEvent`

```ts
interface SubprocessOutcome {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  signal?: NodeJS.Signals | null;
}
```

Parses the captured stdout envelope. If the envelope is valid, returns a `result` or `error` event based on the envelope's `error` field. If the envelope is missing or malformed, synthesizes an `error` event from exit code and the last `STDERR_TAIL_BYTES` (4096) of stderr.

### `parseNdjsonStream(stream, options): AsyncIterable<...>`

```ts
interface ParseNdjsonStreamOptions {
  onParseError?: (raw: string, err: Error) => void;
}
```

Reads a Node `Readable` and yields parsed JSON objects, one per line. Malformed lines invoke `onParseError` (default: silent skip) and are dropped.

### `Transport`

A lower-level wrapper around the engine subprocess that exposes raw stdout/stderr streams. Use only if you need to bypass the default event-loop pipeline.

```ts
class Transport {
  constructor(opts: TransportOptions);
  start(): Promise<void>;
  stop(): Promise<ExitInfo>;
  stdout: NodeJS.ReadableStream;
  stderr: NodeJS.ReadableStream;
}
```

### `makeApprovalHandler(adapter): ApprovalHandler`

Reserved for future approval channel work. Currently constructs an opaque handler. See [Advanced: approval](advanced.md#approval).

### `listModels(params): Promise<ModelsListEnvelope>`

```ts
interface ListModelsParams {
  provider?: 'anthropic' | 'openai' | 'azure-openai' | 'ollama';  // omit for aggregate
  latest?: boolean;
  timeoutMs?: number;
  // ...same env/binary injection as spawnAgent
}
```

Invokes `amplifier-agent models list --output json [...flags]` and parses the result. Returns the same single-provider or aggregate envelope documented in [CLI: models list](../user/cli-reference.md#models-list).

Throws `ListModelsError` if the underlying CLI call exits non-zero or emits malformed JSON.

### `resolveMcpConfigPath(opts) / cleanupSpillFile(path)`

Spill a runtime `mcpServers` object to a `0600` tempfile so the engine can read it via `AMPLIFIER_MCP_CONFIG`. See [Advanced: MCP](advanced.md#mcp-servers).

---

## Public types (summary)

```ts
type DisplayEvent = ...;            // discriminated union, see above
type ApprovalResponse = ...;
type EngineVersionPayload = ...;
type EngineInfo = ...;
type McpSpillResult = ...;
type McpServerConfig = ...;
type ListModelsParams = ...;
type ModelInfo = ...;
type ModelsListEnvelope = ...;
type TransportOptions = ...;
type ExitInfo = ...;
type ParseNdjsonStreamOptions = ...;
type ResolveBinaryPathOptions = ...;
type BuildEnvOptions = ...;
type ApprovalAdapter = ...;
type ApprovalRequest = ...;
type ApprovalHandler = ...;
type ChildProcessFactory = ...;
type AssembleArgvInput = ...;
type SubprocessOutcome = ...;
type VersionCheckOk = ...;
type VersionCheckFail = ...;
type VersionCheckResult = VersionCheckOk | VersionCheckFail;
type CheckProtocolVersionOptions = ...;
type SessionHandleParams = ...;
type SpawnAgentParams = ...;        // see top of this file
```

For exact shapes, see `dist/*.d.ts` in the installed package.
