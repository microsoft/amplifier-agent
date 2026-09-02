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
  const session = await agent.createSession();
  const result = await session.run({ content: [{ type: "text", text: "Say hello." }] });
  console.log(result.state, result.content?.[0].text);
} finally {
  await agent.close();
}
```

`createAgent` gives you a ready agent or throws. `state` is `success`, `failure`,
`rejected`, or `cancelled`. See [turns](../concepts/turns.md).

## Watching the work

`run` waits. `startTurn` lets you watch the same turn happen.

```ts
const turn = await session.startTurn({
  content: [{ type: "text", text: "Summarize CHANGELOG.md." }],
});

let result;
for await (const event of turn.events()) {
  if (event.type === "output_delta") {
    for (const part of event.payload.content) process.stdout.write(part.text);
  } else if (event.type === "tool_call") {
    console.log(`\n[${event.payload.call.source}] ${event.payload.call.name}`);
  } else if (event.type === "terminal") {
    result = event.payload;
  }
}
```

Appending every `output_delta` reconstructs `result.content` exactly. The stream has one
consumer; asking twice throws `stream_already_consumed`. All eleven event types are in
[events](../concepts/events.md).

## A tool your process runs

```ts
import { readFile, stat } from "node:fs/promises";
import { createAgent, ToolFailed } from "@microsoft/amplifier-agent";

const agent = await createAgent({
  provider: "anthropic",
  model: "claude-sonnet-5",
  tools: [{
    name: "read_file",
    description: "Read a UTF-8 text file from disk.",
    inputSchema: {
      $schema: "https://json-schema.org/draft/2020-12/schema",
      type: "object",
      properties: { path: { type: "string" } },
      required: ["path"],
    },
    handler: async ({ path }) => {
      if (!(await stat(path)).isFile()) throw new ToolFailed(`${path} is not a file`);
      return readFile(path, "utf8");
    },
  }],
});
```

Your handler runs in your process and nowhere else. Returning resolves the call
`completed`, `ToolFailed` resolves it `failed`, and `ToolOutcomeUnknown` resolves it
`unknown` when you genuinely cannot tell whether the effect landed. See
[tools](../concepts/tools.md).

## Approving effects

```ts
const agent = await createAgent({
  provider: "anthropic",
  model: "claude-sonnet-5",
  approvals: async (request) => {
    console.log(`${request.name}: ${request.summary}`);
    return { decision: (await confirm()) ? "allow" : "deny" };
  },
});
```

Without a handler, pass `approvals: "allow"` or `approvals: "deny"` and the decision is
made before the turn starts. With neither, a consequential action fails
`approval_unavailable` rather than proceeding. See
[approvals](../concepts/approvals.md).

## Coming back later

```ts
const session = await agent.createSession({ sessionId: "ticket-4417" });
await session.run({ content: [{ type: "text", text: "Start on the login bug." }] });
await session.close();

// another process, another day
const resumed = await agent.resumeSession("ticket-4417");
await resumed.run({ content: [{ type: "text", text: "What did you find?" }] });
```

Sessions are durable by default and resume from the local transcript alone. Creating an
id that exists throws `already_exists`, and resuming an unknown one throws `not_found`.
See [sessions](../concepts/sessions.md).

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

## Next

```
names.md       what each contract name is called here
reference.md   every signature
../concepts/   what any of it means
```
