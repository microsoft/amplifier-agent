import assert from "node:assert/strict";
import { createAgent } from "@microsoft/amplifier-agent";

const agent = await createAgent({ provider: "anthropic", model: "claude-sonnet-5" });
try {
  const session = await agent.createSession({ persistence: "ephemeral" });
  const turn = await session.startTurn({ content: [{ type: "text", text: "Hello" }] });
  const events = [];
  for await (const event of turn.events()) events.push(event);
  const result = events.at(-1).payload;
  assert.equal(events[0].type, "turn_started");
  assert.equal(events.at(-1).type, "terminal");
  assert.equal(result.state, "success");
  assert.deepEqual(result.content.map((part) => part.text), ["Wire ", "reply"]);
  assert.deepEqual(events.filter((event) => event.type === "output_delta")
    .flatMap((event) => event.payload.content), result.content);
  assert.equal(result.usage.entries[0].tokens_in, 20n);
  assert.equal(result.usage.entries[0].tokens_out, 2n);
  assert.deepEqual(session.history[0].result, result);
  await session.close();
} finally {
  await agent.close();
}
console.log("Installed production runtime passed.");
