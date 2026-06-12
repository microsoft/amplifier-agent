# Events

`SessionHandle.submit()` yields a stream of `DisplayEvent` objects — a discriminated union of five types.

```ts
type DisplayEvent =
  | { type: 'init'; sessionId: string }
  | { type: 'activity' }
  | { type: 'result'; text: string }
  | { type: 'error'; code: string; classification: string; severity: 'error';
      correlationId: string; message: string; stderrTail?: string;
      retryable?: boolean }
  | { type: 'notification'; method: string; params: unknown };
```

## Event ordering

Every `submit()` yields events in roughly this order:

```
init                          ← yielded before the subprocess spawn
notification (many)           ← if displayMode: 'ndjson'
  - usage
  - tool/started, tool/completed
  - thinking/delta, thinking/final
  - result/delta, result/final
  - usage (session total)
activity                      ← every ~2 seconds while subprocess alive
result OR error               ← terminal — iterator ends after this
```

The iterator ends when the engine subprocess exits. After a `result` or `error` event, no further events are emitted.

---

## `init`

```ts
{ type: 'init', sessionId: string }
```

Yielded **before** the engine subprocess is spawned. Useful so your UI can show "Session s1 starting…" without waiting for the engine to boot.

The `sessionId` field is exactly what you passed in `SpawnAgentParams.sessionId`.

```ts
for await (const event of handle.submit('Hello')) {
  if (event.type === 'init') {
    console.log('Session:', event.sessionId);
  }
}
```

---

## `activity`

```ts
{ type: 'activity' }
```

A keep-alive heartbeat. Yielded every ~2 seconds while the engine subprocess is alive. Use to keep your UI animated and to detect freezes.

`activity` carries no payload. The fact that it arrives is the signal.

```ts
let lastActivity = Date.now();
for await (const event of handle.submit('Long task')) {
  if (event.type === 'activity') {
    lastActivity = Date.now();
    updateProgressIndicator();
  }
}
```

---

## `result`

```ts
{ type: 'result', text: string }
```

Terminal event. The engine completed successfully and produced `text` as the reply.

This is the same `reply` field from the CLI's `--output json` envelope. If you want streamed text deltas, watch for `notification` events with `method: 'result/delta'` (see below).

```ts
for await (const event of handle.submit('Hello')) {
  if (event.type === 'result') {
    console.log('Final reply:', event.text);
  }
}
```

---

## `error`

```ts
{
  type: 'error',
  code: string,
  classification: 'engine' | 'protocol' | 'approval' | 'transport' | 'unknown',
  severity: 'error',
  correlationId: string,
  message: string,
  stderrTail?: string,
  retryable?: boolean,
}
```

Terminal event. The engine failed.

| Field | Notes |
|---|---|
| `code` | Stable identifier. See [CLI output formats: error codes](../user/output-formats.md#error-codes-reference). |
| `classification` | Drives exit code mapping in the CLI; useful for UI styling here. |
| `correlationId` | Same UUID v4 as in the CLI envelope and audit record. Surface it in bug reports. |
| `message` | Human-readable. Safe to display verbatim. |
| `stderrTail` | Set when the SDK synthesizes the error from exit code + stderr tail (envelope was missing/malformed). Up to `STDERR_TAIL_BYTES` (4096) bytes. |
| `retryable` | Set by the SDK if the error is known-transient. Currently rarely set. |

The SDK distinguishes two error sources:

1. **Envelope error** — the engine produced a valid JSON envelope with `error` populated. All fields come from the engine.
2. **Synthesized error** — the engine crashed before producing an envelope. The SDK builds a fallback from the exit code (mapped to a classification) and the stderr tail.

Both look identical from the consumer's perspective; check `stderrTail` to know which.

---

## `notification`

```ts
{ type: 'notification', method: string, params: unknown }
```

A structured wire event from the engine's NDJSON stderr stream. **Only emitted when `displayMode: 'ndjson'`**. With `displayMode: 'text'` (or unset), no `notification` events are emitted.

The shape mirrors JSON-RPC notifications (without the `"jsonrpc": "2.0"` field — it's NDJSON, not full JSON-RPC).

### Canonical wire methods

| Method | `params` shape | When |
|---|---|---|
| `result/delta` | `{ sessionId, turnId, text }` | A chunk of the model's reply. Fired many times. |
| `result/final` | `{ sessionId, turnId, text }` | End-of-reply marker. `text` typically empty. |
| `tool/started` | `{ sessionId, turnId, tool, ... }` | A tool call started. |
| `tool/completed` | `{ sessionId, turnId, tool, result, ... }` | A tool call completed. |
| `thinking/delta` | `{ sessionId, turnId, text }` | Extended-thinking chunk. |
| `thinking/final` | `{ sessionId, turnId, text }` | End-of-thinking. |
| `usage` (per-call) | `{ sessionId, turnId, inputTokens, outputTokens, llmDurationMs, model, provider, cacheReadTokens, cacheWriteTokens, cost }` | After each LLM call. |
| `usage` (session total) | `{ sessionId, turnId, inputTokens: 0, outputTokens: 0, sessionCostTotal }` | At session end. |

These are emitted whenever `displayMode === 'ndjson'`, regardless of verbosity flags (the verbosity flags only affect `--display text`).

### Streaming the reply

```ts
let buffer = '';
for await (const event of handle.submit('Tell me a story')) {
  if (event.type === 'notification' && event.method === 'result/delta') {
    const delta = (event.params as any).text as string;
    buffer += delta;
    process.stdout.write(delta);   // stream to console
  } else if (event.type === 'result') {
    console.log('\n\nFull reply length:', buffer.length);
  }
}
```

### Tracking tool calls

```ts
for await (const event of handle.submit('Search the codebase')) {
  if (event.type === 'notification') {
    if (event.method === 'tool/started') {
      const params = event.params as any;
      console.log(`Tool started: ${params.tool}`);
    } else if (event.method === 'tool/completed') {
      const params = event.params as any;
      console.log(`Tool completed: ${params.tool}`);
    }
  }
}
```

### Token accounting

```ts
let totalIn = 0, totalOut = 0;
for await (const event of handle.submit('Hello')) {
  if (event.type === 'notification' && event.method === 'usage') {
    const params = event.params as any;
    if (typeof params.inputTokens === 'number') totalIn += params.inputTokens;
    if (typeof params.outputTokens === 'number') totalOut += params.outputTokens;
    if (params.sessionCostTotal) {
      console.log('Session cost:', params.sessionCostTotal);
    }
  }
}
console.log('Total tokens:', totalIn, '/', totalOut);
```

---

## Event source map

For debugging, here's where each event comes from:

| Event | Source |
|---|---|
| `init` | Synthesized by the SDK before spawn. |
| `activity` | Synthesized by the SDK on a 2s timer. |
| `notification` | Parsed from the engine's stderr (`--display ndjson` lines). |
| `result` | Parsed from the engine's stdout envelope `reply` field (when `error: null`). |
| `error` | Either the engine's envelope `error` field, or synthesized from exit code + stderr tail. |

If you're seeing fewer events than expected, check:

- `displayMode: 'ndjson'` is set — without it, no `notification` events.
- The engine actually finished — `activity` events should be visible during long runs.
- Your iterator's `if/else` chain handles `init`/`activity` (otherwise they'll seem invisible).
