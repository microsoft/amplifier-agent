import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const packageRoot = fileURLToPath(new URL("../../", import.meta.url));
const engineRoot = fileURLToPath(new URL("../../../engine/src/amplifier_agent_engine/_node_host/", import.meta.url));

test("contract: replacing the complete host participant preserves binding calls and engine decisions", { timeout: 20_000 }, async () => {
  const consumer = await mkdtemp(path.join(tmpdir(), "amplifier-host-owner-"));
  try {
    await cp(path.join(packageRoot, "src"), path.join(consumer, "src"), { recursive: true });
    await writeFile(path.join(consumer, "package.json"), '{"type":"module"}');
    await symlink(path.join(packageRoot, "node_modules"), path.join(consumer, "node_modules"), "dir");
    await mkdir(path.join(consumer, "runtime/linux-x64/node-host"), { recursive: true });
    await writeFile(path.join(consumer, "runtime/linux-x64/node-host/index.mjs"), `
export const observed = [];
export const failure = { code: "org.example.engine_decision", category: "executor",
  message: "Engine-owned callback settlement.", remedy: "Read the engine's receipt.",
  retryable: false, correlation_id: "engine-receipt", details: { exact: 9007199254740993123n },
  "org.example.error": { retained: true } };
export async function createAgent(options, bridge, versions) {
  observed.push({ options, versions });
  const info = { session_id: "engine-session", persistence: "durable" };
  const identity = { session_id: info.session_id, turn_id: "engine-turn" };
  const invoke = (frame) => new Promise(resolve => bridge.dispatch(frame, resolve));
  let closed = false;
  const events = async () => {
    const tool = await invoke({ event: "callback", callback_id: "callback", turn_id: identity.turn_id,
      kind: "tool", args: { name: "receipt", arguments: { exact: 9007199254740993123n }, context: { call_id: "engine-call" } } });
    const approval = await invoke({ event: "callback", callback_id: "approval", turn_id: identity.turn_id,
      kind: "approval", args: { request: { request_id: "engine-approval", call: { call_id: "engine-call", name: "receipt", source: "caller", arguments: {} }, summary: "Approve receipt." } } });
    observed.push({ tool, approval });
    const values = [
      ["turn_started", { continuation: "fresh", primary_actual: { provider: "anthropic", model: "claude-sonnet-5" } }],
      ["org.example.terminal", { state: "success", exact: 9007199254740993123n }],
      ["terminal", { state: "failure", error: failure }],
    ];
    return values.map(([type, payload], index) => ({ ...identity, contract_version: "turn-events/1", sequence: BigInt(index + 1), type, payload }));
  };
  const session = {
    info, history: [],
    async run(input) { observed.push({ run: input }); return { state: "failure", error: failure }; },
    async start_turn(input) { observed.push({ start_turn: input }); return {
      info: identity, events() { return { async *[Symbol.asyncIterator]() { yield* await events(); } }; },
      async cancel() { observed.push({ cancel: true }); },
    }; },
    async fork() { observed.push({ fork: true }); return session; },
    async close() { observed.push({ session_close: true }); },
  };
  return {
    async create_session(options) { observed.push({ create_session: options }); return session; },
    async resume_session(id) { observed.push({ resume_session: id }); return session; },
    async list_sessions() { if (closed) throw failure; return [info]; },
    async delete_session(id) { observed.push({ delete_session: id }); },
    async close() { await bridge.settled(); closed = true; observed.push({ agent_close: true }); },
  };
}
`);
    await writeFile(path.join(consumer, "consumer.mjs"), `
import assert from "node:assert/strict";
import { createAgent, AgentError, ToolFailed, contractVersions } from "./src/index.ts";
import { observed, failure } from "./runtime/linux-x64/node-host/index.mjs";
const agent = await createAgent({ toolErrorPolicy: "continue", mcpServers: [],
  approvals: async () => ({ decision: "engine-must-validate" }),
  tools: [{ name: "receipt", description: "Receipt.", inputSchema: { type: "object" },
    handler: async args => { assert.equal(args.exact, 9007199254740993123n); throw new ToolFailed("Caller receipt."); } }] });
assert.deepEqual(observed[0].versions, contractVersions);
assert.equal(observed[0].options.options.tool_error_policy, "continue");
assert.deepEqual(observed[0].options.options.mcp_servers, []);
assert.equal("handler" in observed[0].options.options.tools[0], false);
assert.deepEqual(observed[0].options.callback_tools, ["receipt"]);
assert.equal(observed[0].options.callback_approvals, true);
const session = await agent.createSession({ sessionId: "caller-session", persistence: "durable" });
assert.deepEqual(observed[1], { create_session: { session_id: "caller-session", persistence: "durable" } });
assert.equal(session.info.session_id, "engine-session");
assert.deepEqual(session.history, []);
const turn = await session.startTurn({ content: [{ type: "text", text: "Invoke." }] });
assert.equal(turn.info.turn_id, "engine-turn");
const values = [];
for await (const event of turn.events()) values.push(event);
assert.deepEqual(values.map(event => event.type), ["turn_started", "org.example.terminal", "terminal"]);
assert.deepEqual(values.map(event => event.sequence), [1n, 2n, 3n]);
assert.equal(values[1].payload.exact, 9007199254740993123n);
assert.equal(values[2].payload.state, "failure");
const inspect = error => {
  assert.ok(error instanceof AgentError);
  for (const [key, value] of Object.entries(failure)) assert.deepEqual(error[key], value);
};
inspect(values[2].payload.error);
assert.equal(observed[3].tool.error.kind, "tool_failed");
assert.equal(observed[3].approval.result.decision, "engine-must-validate");
await turn.cancel();
inspect((await session.run({ content: [] })).error);
await session.fork();
await agent.resumeSession("saved-session");
assert.equal((await agent.listSessions())[0].session_id, "engine-session");
await agent.deleteSession("saved-session");
await session.close();
await agent.close();
await assert.rejects(agent.listSessions(), error => { inspect(error); return true; });
for (const key of ["cancel", "run", "fork", "resume_session", "delete_session", "session_close", "agent_close"]) {
  assert.ok(observed.some(item => key in item), key);
}
`);
    const result = await execute(process.execPath, ["--import", "tsx", "consumer.mjs"], {
      cwd: consumer, timeout: 15_000, env: { ...process.env },
    });
    assert.equal(result.stderr, "");
  } finally { await rm(consumer, { recursive: true, force: true }); }
});

test("contract: the Node host implementation imports only engine-owned modules and Node builtins", async () => {
  const modules = ["index", "connection", "events", "records", "supervision"];
  for (const name of modules) {
    const source = await readFile(path.join(engineRoot, `${name}.mjs`), "utf8");
    const imports = [...source.matchAll(/\bfrom\s+["']([^"']+)["']/g)].map(match => match[1]!);
    for (const dependency of imports) {
      assert.ok(dependency.startsWith("node:") || modules.some(module => dependency === `./${module}.mjs`), `${name}: ${dependency}`);
    }
    assert.doesNotMatch(source, /amplifier_agent\b|amplifier_agent_http\b|@microsoft\/amplifier-agent|typescript\/src/);
  }
});
