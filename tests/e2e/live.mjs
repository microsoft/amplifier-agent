import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { createAgent } from "@microsoft/amplifier-agent";

const resume = process.env.E2E_MODE === "resume";
const effects = [];
const normalize = value => JSON.parse(JSON.stringify(value, (_, item) =>
  typeof item === "bigint" ? String(item) : item));
const agent = await createAgent({
  tools: [{ name: "record_probe", description: "Record a harmless in-memory probe",
    inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object",
      properties: { value: { type: "string" } }, required: ["value"] },
    handler: async arguments_ => { effects.push(arguments_); return "Probe recorded successfully."; },
  }],
  approvals: async request => ({ decision: request.name === "record_probe" ? "allow" : "deny" }),
  instructions: "Use only record_probe when asked for a tool. Keep responses short.",
});
try {
  const session = resume ? await agent.resumeSession("live-session")
    : await agent.createSession({ sessionId: "live-session" });
  if (resume) assert.deepEqual(normalize(session.history),
    JSON.parse(await readFile(process.env.E2E_EXPECTED_HISTORY, "utf8")));
  const prompt = resume
    ? "Which value did record_probe record? Reply with only that value; do not call tools."
    : "Call record_probe exactly once with value 'ok', then reply OK.";
  const turn = await session.startTurn({ content: [{ type: "text", text: prompt }] });
  const events = [];
  for await (const event of turn.events()) events.push(event);
  const result = events.at(-1).payload;
  assert.equal(result.state, "success", result.error?.code);
  assert.equal(events[0].type, "turn_started");
  assert.equal(events.at(-1).type, "terminal");
  assert.ok(events.some(event => event.type === "output_delta"));
  assert.deepEqual(session.history.at(-1).result, result);
  assert.equal(session.history.length, resume ? 2 : 1);
  assert.deepEqual(effects, resume ? [] : [{ value: "ok" }]);
  assert.ok(result.usage.entries.length);
  if (resume) {
    assert.equal(events[0].payload.continuation, "resumed");
    assert.ok(result.content.map(part => part.text).join("").toLowerCase().includes("ok"));
  } else await writeFile(process.env.E2E_EXPECTED_HISTORY, JSON.stringify(normalize(session.history)));
  await session.close();
  console.log(JSON.stringify({ kind: "result", state: result.state, streamed: true,
    effects: effects.length, history_count: resume ? 2 : 1, pid: process.pid }));
} finally { await agent.close(); }
