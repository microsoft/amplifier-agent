# TypeScript SDK quickstart

Install `amplifier-agent-ts`, spawn the agent, drive a turn — in a Node script.

## Prerequisites

- **Node ≥ 20** — the package is ESM-only.
- **The `amplifier-agent` binary on PATH.** Install with `uv tool install --from git+https://github.com/microsoft/amplifier-agent amplifier-agent`. See [Installation](../user/installation.md).
- **A provider API key.** Default provider is Anthropic; set `ANTHROPIC_API_KEY`.

## 1. Install

```bash
npm install amplifier-agent-ts
```

The SDK depends only on Node's built-in `child_process` and `node:stream` modules. No transitive dependencies.

## 2. Hello world

```ts
// hello.mjs
import {
  spawnAgent,
  DEFAULT_ALLOWLIST,
} from 'amplifier-agent-ts';

const handle = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: 'hello-1',
  resume: false,
  approval: { mode: 'yes' },
  displayMode: 'ndjson',
  // DEFAULT_ALLOWLIST = [PATH, HOME, USER, LANG, TERM, TMPDIR]
  // + AMPLIFIER_*, LC_* always allowed.
  // Provider keys need to be added explicitly:
  env: { allowlist: [...DEFAULT_ALLOWLIST, 'ANTHROPIC_API_KEY'] },
});

console.log('Engine:', handle.getEngineInfo());

for await (const event of handle.submit('Reply with only the word: pong')) {
  if (event.type === 'init') {
    console.log('[init] session', event.sessionId);
  } else if (event.type === 'notification') {
    console.log('[wire]', event.method, event.params);
  } else if (event.type === 'result') {
    console.log('[reply]', event.text);
  } else if (event.type === 'error') {
    console.error('[error]', event.code, event.message);
    process.exit(1);
  }
  // 'activity' events fire every 2s while the engine is alive — useful for
  // keep-alive UI, skipped here for brevity.
}
```

Run it:

```bash
node hello.mjs
```

Expected output:

```
Engine: {
  binaryPath: '/Users/you/.local/bin/amplifier-agent',
  protocolVersion: '0.3.0',
  engineVersion: '0.5.2',
  bundleDigest: ''
}
[init] session hello-1
[wire] usage { sessionId: '', turnId: 'turn-1', inputTokens: 4202, ... }
[wire] result/final { sessionId: '', turnId: 'turn-1', text: '' }
[wire] result/delta { sessionId: '', turnId: 'turn-1', text: 'pong' }
[wire] usage { sessionId: '', turnId: 'turn-1', inputTokens: 0, sessionCostTotal: '...' }
[reply] pong
```

## 3. Resume the session

Each `submit()` is one turn. To continue the same conversation, create a new `SessionHandle` with the same `sessionId` and `resume: true`:

```ts
const turn2 = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: 'hello-1',
  resume: true,
  approval: { mode: 'yes' },
  displayMode: 'ndjson',
  env: { allowlist: [...DEFAULT_ALLOWLIST, 'ANTHROPIC_API_KEY'] },
});

for await (const event of turn2.submit('What was your last reply?')) {
  if (event.type === 'result') {
    console.log('[reply]', event.text);  // "My last reply was 'pong'."
  }
}
```

## 4. Cancel a running turn

```ts
const handle = await spawnAgent({ /* ... */ });

const iter = handle.submit('Do something long');
setTimeout(() => handle.cancel(), 2000);  // SIGTERM after 2s

try {
  for await (const event of iter) {
    // ...
  }
} catch (err) {
  console.log('Cancelled:', err.message);
}
```

`cancel()` sends `SIGTERM` to the engine's process group, waits 5 seconds, then `SIGKILL`s if still alive. The next iteration of the event loop will see an `error` event with `code: "cancelled"` (or similar) and the loop will end.

## 5. Handle errors

The SDK surfaces three kinds of failures:

```ts
import { AaaError } from 'amplifier-agent-ts';

try {
  const handle = await spawnAgent({ /* bad params */ });
} catch (err) {
  if (err instanceof AaaError) {
    console.error(err.code);             // e.g. 'env_injection_rejected'
    console.error(err.classification);   // 'protocol' | 'engine' | 'approval' | ...
    console.error(err.message);
    console.error(err.remediation);
  }
}
```

Inside the event loop, an `error` event has the same `code` and `classification` fields. See [Events](events.md#error-event) and [CLI output formats: error codes](../user/output-formats.md#error-codes-reference).

## Next steps

- Full API surface: [API reference](api-reference.md).
- Streaming events in detail: [Events](events.md).
- Approval handling, MCP servers, custom binaries: [Advanced](advanced.md).
