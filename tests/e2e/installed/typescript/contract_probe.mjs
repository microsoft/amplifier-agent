import assert from "node:assert/strict";
import { createAgent } from "@microsoft/amplifier-agent";

const probe = JSON.parse(process.env.E2E_CONTRACT_PROBE);
const effects = [];
const options = {
  tools: probe.tool ? [{
    name: "observe", description: "Observe",
    inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" },
    handler: async (_arguments, context) => {
      effects.push(context.call_id);
      return "Confirmed effect";
    },
  }] : [],
  approvals: "allow",
};
let agent;
try { agent = await createAgent(options); }
catch (error) {
  assert.equal(probe.mode, "invalid_host");
  console.log(JSON.stringify({ kind: "error", error: {
    code: error.code, category: error.category, message: error.message,
    remedy: error.remedy, retryable: error.retryable,
  } }));
}
if (agent) {
  assert.notEqual(probe.mode, "invalid_host");
  const events = [], histories = [];
  try {
    let session = await agent.createSession({ sessionId: "installed-contract" });
    for (let index = 0; index < probe.rounds; index++) {
      if (index && probe.mode === "restart") {
        await session.close();
        await agent.close();
        agent = await createAgent(options);
        session = await agent.resumeSession("installed-contract");
      }
      const turn = await session.startTurn({ content: [{ type: "text", text: `Visible question ${index}` }] });
      const observed = [];
      for await (const event of turn.events()) observed.push(event);
      assert.deepEqual(observed.map((event) => event.sequence), observed.map((_, i) => BigInt(i + 1)));
      assert.equal(observed[0].type, "turn_started");
      assert.equal(observed.at(-1).type, "terminal");
      const result = observed.at(-1).payload;
      assert.equal(result.state, "success", JSON.stringify(result.error));
      assert.equal(result.content.map((part) => part.text).join(""), "Wire reply");
      assert.deepEqual(session.history.at(-1).result, result);
      events.push(observed.map((event) => event.type));
      histories.push(session.history.length);
    }
    await session.close();
  } finally { await agent.close(); }
  console.log(JSON.stringify({ kind: "done", events, histories, effects }));
}
