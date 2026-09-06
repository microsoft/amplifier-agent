# Amplifier Agent TypeScript binding

Typed agents, sessions, turns, tools, approvals, and events for Node applications.
Requires Node 22 on Linux x86-64 with glibc 2.35 or newer, including compatible
WSL2 distributions. The ESM package includes its execution runtime; consumers do
not need Python or uv.

Follow the [installation guide](https://github.com/microsoft/amplifier-agent/blob/v1/docs_v1/install.md#typescript).
Set `ANTHROPIC_API_KEY` in the environment. Save this as `hello.mjs` and run
`node hello.mjs`:

```js
import { createAgent } from "@microsoft/amplifier-agent";

const agent = await createAgent({ provider: "anthropic", model: "claude-sonnet-5" });
try {
  const session = await agent.createSession({ persistence: "ephemeral" });
  const result = await session.run({ content: [{ type: "text", text: "Say hello." }] });
  if (result.error) throw new Error(`${result.error.message} ${result.error.remedy}`);
  console.log((result.content ?? []).map(part => part.text).join(""));
} finally {
  await agent.close();
}
```

Sessions are durable by default. Tool execution, including file reads, requires an
approval policy. See the
[TypeScript quickstart](https://github.com/microsoft/amplifier-agent/blob/v1/docs_v1/typescript/quickstart.md)
for streaming, tools, approvals, and resuming sessions. Native Windows, macOS,
ARM64, and Alpine/musl are outside the bundled runtime's platform support.
