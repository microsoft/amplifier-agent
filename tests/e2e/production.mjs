import assert from "node:assert/strict";
import { appendFile, open, readFile } from "node:fs/promises";
import { createAgent } from "@microsoft/amplifier-agent";

const mode = process.env.E2E_MODE;
const provider = process.env.AMPLIFIER_AGENT_PROVIDER;
const model = process.env.AMPLIFIER_AGENT_MODEL;
const normalize = (value) => JSON.parse(JSON.stringify(value, (_, item) =>
  typeof item === "bigint" || typeof item === "number" ? String(item) : item));
const output = (value) => console.log(JSON.stringify(normalize(value)));
let approved = false;
const ephemeral = mode.startsWith("ephemeral");
const tools = ephemeral ? [] : [{
  name: "counter", description: "Record an effect",
  inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" },
  handler: async (arguments_, context) => {
    assert.ok(approved);
    await appendFile(process.env.E2E_EFFECT_LEDGER, JSON.stringify({ pid: process.pid, call_id: context.call_id }) + "\n");
    const ledger = await open(process.env.E2E_EFFECT_LEDGER, "a");
    try { await ledger.sync(); } finally { await ledger.close(); }
    return "effect-recorded";
  },
}];
const agent = await createAgent({ provider, model, tools, approvals: async () => {
  approved = true;
  return { decision: "allow" };
} });
try {
  const session = mode === "resume"
    ? await agent.resumeSession(process.env.E2E_SESSION_ID)
    : await agent.createSession(ephemeral ? { persistence: "ephemeral" } : { sessionId: process.env.E2E_SESSION_ID });
  const before = normalize(session.history);
  if (mode === "resume") assert.deepEqual(before, JSON.parse(await readFile(process.env.E2E_EXPECTED_HISTORY, "utf8")));
  const turn = await session.startTurn({ content: [{ type: "text", text: mode === "resume" ? "Resume this conversation" : "Hello" }] });
  const events = [];
  for await (const event of turn.events()) {
    events.push(event);
    if (event.type === "output_delta") {
      output({ kind: "output", content: event.payload.content });
      if (mode === "ephemeral_cancel") await turn.cancel();
    }
  }
  const result = events.at(-1).payload;
  assert.equal(events[0].type, "turn_started");
  assert.equal(events.at(-1).type, "terminal");
  const expectedState = mode === "ephemeral_cancel" ? "cancelled" : mode.endsWith("failure") ? "failure" : "success";
  assert.equal(result.state, expectedState);
  if (expectedState === "success") assert.equal(result.content.map((part) => part.text).join(""), "Wire reply");
  else {
    assert.ok(result.error.remedy);
    assert.equal(result.error.code, expectedState === "cancelled" ? "turn_cancelled" : "provider_failed");
    if (mode === "ephemeral_cancel" || mode === "ephemeral_partial_failure") assert.equal(result.content.map((part) => part.text).join(""), "Wire ");
  }
  assert.deepEqual(events.map((event) => event.sequence), events.map((_, index) => BigInt(index + 1)));
  assert.deepEqual(events.filter((event) => event.type === "output_delta")
    .flatMap((event) => event.payload.content), result.content);
  assert.equal(result.usage.entries[0].provider, provider);
  assert.equal(result.usage.entries[0].model, model);
  if (expectedState === "success") {
    assert.equal(result.usage.entries[0].tokens_in, mode === "create" ? 40n : 20n);
    assert.equal(result.usage.entries[0].tokens_out, mode === "create" ? 4n : 2n);
  }
  assert.deepEqual(session.history.at(-1).result, result);
  if (mode === "create") for (const kind of ["tool_call", "tool_result", "approval_request", "approval_decision"]) {
    assert.equal(events.filter((event) => event.type === kind).length, 1);
  }
  if (mode === "resume") {
    assert.equal(events[0].payload.continuation, "resumed");
    assert.equal(events.filter((event) => event.type === "tool_call").length, 0);
  }
  output({ kind: "result", pid: process.pid, before, history: session.history, result });
  if (mode === "create") await new Promise(() => { setInterval(() => {}, 1000); });
  await session.close();
} finally {
  await agent.close();
}
