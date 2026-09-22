# TypeScript quickstart

Assumes [install](../install.md) and a provider credential in your environment.

## One turn

```ts
import { createAgent } from "@microsoft/amplifier-agent";

const agent = await createAgent({
  provider: "anthropic",
  model: "claude-sonnet-5",
});

try {
  const session = await agent.createSession({ persistence: "ephemeral" });
  const result = await session.run({ content: [{ type: "text", text: "Say hello." }] });
  console.log(result.state, result.content?.map(part => part.text).join(""));
  if (result.error) console.error(result.error.code, result.error.message, result.error.remedy);
} finally {
  await agent.close();
}
```

`createAgent` gives you a ready agent or throws. `state` is `success`, `failure`,
`rejected`, or `cancelled`. See [turns](../concepts/turns.md).
This example uses an ephemeral conversation, whose lifetime ends at close.

Omit `provider` or `model` to use its resolved [host configuration](../configuration.md).
The following snippets assume open agent or session handles and belong before their
enclosing `finally` closes them. Keep imports at module scope and use ESM for
top-level `await`.

## Watching the work

`run` waits. `startTurn` lets you watch the same turn happen.

```ts
const turn = await session.startTurn({
  content: [{ type: "text", text: "Explain what an agent session is." }],
});

for await (const event of turn.events()) {
  if (event.type === "output_delta") {
    for (const part of event.payload.content) process.stdout.write(part.text);
  } else if (event.type === "tool_call") {
    console.log(`\n[${event.payload.call.source}] ${event.payload.call.name}`);
  } else if (event.type === "terminal") {
    const result = event.payload;
    if (result.error) console.error(`\n${result.error.code}: ${result.error.remedy}`);
  }
}
```

Appending every `output_delta` reconstructs `result.content` exactly. The stream has one
consumer; asking twice throws `stream_already_consumed`. All eleven event types are in
[events](../concepts/events.md).
To stop early, call `await turn.cancel()` and keep consuming through `terminal`.
Leaving the loop alone does not cancel the turn.

## A tool your process runs

```ts
import { readFile } from "node:fs/promises";
import { BUILTIN_TOOLS, ToolFailed, type Tool } from "@microsoft/amplifier-agent";

const readNoteTool: Tool = {
  name: "read_note",
  description: "Read a UTF-8 note from disk.",
  inputSchema: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    type: "object",
    properties: { path: { type: "string" } },
    required: ["path"],
    additionalProperties: false,
  },
  handler: async ({ path }, context) => {
    console.log(`Reading file for call ${context.call_id}`);
    if (typeof path !== "string") throw new ToolFailed("path must be a string");
    try {
      return await readFile(path, "utf8");
    } catch (error) {
      throw new ToolFailed(`Cannot read ${path}: ${String(error)}`);
    }
  },
};
```

Pass `tools: [...BUILTIN_TOOLS, readNoteTool]` when constructing the agent, together
with an approval policy from the next example. `tools` is the whole set, so spread
`BUILTIN_TOOLS` to keep the
[built-in tools](../concepts/tools.md#built-in-tools-and-skills).

Your handler runs in your process and nowhere else. Returning resolves the call
`completed`, `ToolFailed` resolves it `failed`, and `ToolOutcomeUnknown` resolves it
`unknown` when you genuinely cannot tell whether the effect landed. See
[tools](../concepts/tools.md).

## Approving effects

```ts
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import type { ApprovalHandler } from "@microsoft/amplifier-agent";

const approve: ApprovalHandler = async (request) => {
  const terminal = createInterface({ input: stdin, output: stdout });
  try {
    console.log(`${request.name}: ${request.summary}`);
    const answer = await terminal.question("[y/N] ");
    return { decision: answer === "y" ? "allow" : "deny" };
  } finally {
    terminal.close();
  }
};
```

Pass `approvals: approve` when constructing the agent. This handler needs an
interactive terminal; a server application can ask through its own interface.

Without a handler, pass `approvals: "allow"` or `approvals: "deny"` and the decision is
made before the turn starts. With neither, a consequential action fails
`approval_unavailable` rather than proceeding. See
[approvals](../concepts/approvals.md).

## Supplying a conversation

```ts
const session = await agent.createSession({ persistence: "ephemeral" });
try {
  const result = await session.run({
    content: [],
    history: [
      { role: "user", content: [{ type: "text", text: "My name is Ada." }] },
      { role: "assistant", content: [{ type: "text", text: "Hello, Ada." }] },
      { role: "user", content: [{ type: "text", text: "What is my name?" }] },
    ],
  });
  console.log(result.state, result.content);
} finally {
  await session.close();
}
```

The complete conversation goes in `history`; empty `content` adds no user message.
Supply history only before an ephemeral session has accepted a turn and when it has no
inherited conversation. See [turns](../concepts/turns.md#supplying-a-conversation).

## Coming back later

```ts
const session = await agent.createSession({ sessionId: "ticket-4417" });
await session.run({ content: [{ type: "text", text: "Start on the login bug." }] });
await session.close();

// another process, another day
const resumed = await agent.resumeSession("ticket-4417");
try {
  const result = await resumed.run({ content: [{ type: "text", text: "What did you find?" }] });
  console.log(result.state, result.content);
} finally {
  await resumed.close();
}
```

Sessions are durable by default and resume from the local transcript alone. Creating an
id that exists throws `already_exists`, and resuming an unknown one throws `not_found`.
See [sessions](../concepts/sessions.md).
Use the same `storage` root and configured `workspace` in both processes, and
reconstruct the agent's credentials, tools, and approval policy. Closing an agent
closes its sessions without deleting durable transcripts.

## Failures

```ts
import { AgentError } from "@microsoft/amplifier-agent";

try {
  await agent.resumeSession("ticket-4417");
} catch (err) {
  if (err instanceof AgentError) console.log(err.code, err.remedy);
  else throw err;
}
```

`remedy` is always present and always actionable. Failures thrown before the stream
exists surface at the method; failures after it exists arrive in `terminal`. See
[errors](../concepts/errors.md).
`run` returns those terminal failures as `TurnResult`; check `result.state` and
`result.error` as well as catching `AgentError`.

## Next

```
names.md       what each contract name is called here
reference.md   every signature
../concepts/   what any of it means
```
