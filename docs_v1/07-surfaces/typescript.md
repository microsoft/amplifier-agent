# TypeScript

An SDK for Node. It spawns the CLI and speaks its contract, so it needs both the
npm package and the agent installed.

```bash
npm install @microsoft/amplifier-agent
```

```typescript
import { createAgent } from "@microsoft/amplifier-agent";

const agent = await createAgent({
  provider: { name: "anthropic", model: "claude-sonnet-5" },
});

const session = await agent.createSession();
const result = await session.run("Summarize src/parser.py");
console.log(result.reply);
```

## Shape

The API mirrors the library, in TypeScript naming. `createAgent`,
`createSession`, `resumeSession`, `listSessions`, `deleteSession`, `listSkills`,
`run`, `stream`, `cancel`, `fork`, `close`. The types are the
[interface](../05-interface/index.md) types with camelCase fields.

`stream` is an async iterable, so the Python and TypeScript reading loops have
the same shape:

```typescript
for await (const event of session.stream("Refactor the parser.")) {
  if (event.type === "message/delta") process.stdout.write(event.text);
  if (event.type === "tool/call") console.error(`\n[${event.name}]`);
}
```

## How it works

Each turn is one CLI invocation. The wrapper assembles argv, spawns the process,
and reads both of its streams.

```
argv      amplifier-agent run --output json --display ndjson --surface-version 1 ...
stdout    one result envelope, at the end
stderr    one event frame per line, as the turn runs
stdin     approval decisions, one per line
```

The envelope becomes `TurnResult`. The stderr frames become the events `stream`
yields. `run` is `stream` consumed to completion, the same as in the library.

Values too large or too awkward for argv are spilled to temporary files and
passed by path:

```
prompt        --prompt-file    long prompts, and prompts with shell metacharacters
mcp servers   --mcp-config     the McpServerConfig list, serialized
config        --config         everything else in AgentConfig
```

Spill files are created with owner-only permissions and removed when the process
exits. They hold prompts and MCP server environments, which routinely contain
credentials.

A session is server state, so `createSession` allocates an id and every
subsequent `run` passes `--session-id` and `--resume`. The conversation lives on
disk between invocations rather than in the wrapper.

## Approvals

Approvals are a callback in the library, and a callback needs a channel back
into a running turn. The wrapper has one: requests arrive as events on stderr,
decisions go back on stdin.

```typescript
const agent = await createAgent({
  provider: { name: "anthropic" },
  approvals: async (request) => {
    if (AUTO_ALLOW.has(request.toolName)) return { action: "allow" };
    return { action: "deny", reason: "not in the allowlist" };
  },
});
```

```
stderr  {"type":"approval/requested","approval_id":"a1","tool_name":"write_file",...}
stdin   {"approval_id":"a1","action":"deny","reason":"not in the allowlist"}
```

Decisions are correlated by `approval_id`, so a handler that takes its time does
not block the frames still arriving. A handler that does not answer within
`timeout_ms` produces `tool/approval_timeout`, which the agent treats as a
failed call rather than an approval.

Omitting `approvals` denies every request that needs one, matching the library.
The wrapper does not substitute a permissive default when no handler is
supplied, because a surface that quietly resolves approvals on your behalf is a
weaker product wearing the same name.

## Tools

Built-in and MCP tools work as they do everywhere. `mcpServers` on the config is
serialized to a spill file and passed through.

Host tools are a Python callable, and there is no Python process here to hold
one. Contribute tools from Node through an MCP server, which is the same flat
set to the model and the same `tool/call` events with `source: "mcp"` to you.

`tools.allow` and `tools.deny` filter by name across every source, unchanged.

## Versioning

The wrapper asserts the CLI surface version it was built against, and the two
compare it exactly.

```
--surface-version 1
```

Mismatched versions refuse each other before the turn starts rather than
negotiating down, because a partial agreement between two halves of one product
is worse than a clean failure. The package exports `version`, `surfaceVersion`,
and `contractVersion` so a caller can report all three without spawning
anything.

There is no capability handshake. A wrapper is a pipe, and the rule that keeps a
pipe working is:

```
Ignore fields you do not recognize. Forward event types you do not
recognize, unchanged.
```

An unknown event type surfaces as `{ type: string, ... }` rather than being
dropped or throwing. That is what lets the agent gain an event without every
wrapper needing a release first, and additive changes are the common case.

## Errors

`AgentError` is a TypeScript `Error` subclass carrying the same closed set of
[codes](../05-interface/errors.md), with `retryable` and `details`.

```typescript
try {
  await session.run(prompt);
} catch (err) {
  if (err instanceof AgentError && err.retryable) await backoff();
  else throw err;
}
```

The raise-or-emit rule is the library's. A failure before the turn starts
rejects the promise. A failure during it arrives as an `error` event and on
`TurnResult.error`, and `run` re-raises only when `stopReason` is `"error"` and
there is no reply.

Two failures belong to this surface rather than the agent, and both are
`internal` with the cause in `details`: the CLI could not be spawned, and its
output could not be parsed. A non-JSON line on stdout is a corrupted envelope
rather than something to guess at, so the wrapper reports it instead of
recovering.
