import assert from "node:assert/strict";
import { appendFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { AgentError, createAgent } from "@microsoft/amplifier-agent";
import type { Agent, AgentOptions, ApprovalResponse, Event, Turn, TurnResult } from "@microsoft/amplifier-agent";
import { assertApprovalOutcome } from "./approval.js";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };
const schema = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };
const scripted = (steps: Record<string, unknown>[]) => "conformance-script:" + JSON.stringify(steps);

async function eventsOf(turn: Turn): Promise<Event[]> {
  const events: Event[] = [];
  for await (const event of turn.events()) events.push(event);
  return events;
}

async function run(agent: Agent, steps: Record<string, unknown>[]): Promise<Event[]> {
  const session = await agent.createSession({ persistence: "ephemeral" });
  try {
    const turn = await session.startTurn({ content: [{ type: "text", text: scripted(steps) }] });
    return await eventsOf(turn);
  } finally { await session.close(); }
}

for (const decision of ["allow", "deny"] as const) {
  test(`run exposes bounded approval details before caller work: ${decision}`, { timeout: 20_000 }, async () => {
    const arguments_ = {
      path: "receipts/order-4417.txt",
      access_token: "private-receipt-credential",
      content: "receipt body ".repeat(2_000),
    };
    const effects: unknown[] = [];
    const summaries: string[] = [];
    const agent = await createAgent({
      ...selection,
      approvals: async (request) => {
        assert.equal(request.name, "save_receipt");
        assert.equal(effects.length, 0);
        assert.ok(request.summary.includes(arguments_.path));
        assert.ok(!request.summary.includes(arguments_.access_token));
        assert.ok(request.summary.length <= 4_096);
        summaries.push(request.summary);
        return { decision };
      },
      tools: [{ name: "save_receipt", description: "Store a receipt.", inputSchema: schema,
        handler: async (arguments_) => { effects.push(arguments_); return "Saved"; } }],
    });
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const result = await session.run({ content: [{ type: "text", text: scripted([
        { tool: { name: "save_receipt", arguments: arguments_ } }, { text: "Done" },
      ]) }] });
      assert.equal(summaries.length, 1);
      assert.equal(result.state, decision === "allow" ? "success" : "rejected");
      assert.deepEqual(effects, decision === "allow" ? [arguments_] : []);
      if (decision === "deny") assert.equal(result.error?.code, "approval_denied");
      await session.close();
    } finally { await agent.close(); }
  });
}

for (const denyHook of [false, true]) {
  test(`named skill agents execute scoped approved hooks${denyHook ? " with denial" : ""}`, { timeout: 20_000 }, async () => {
    const folder = await mkdtemp(join(tmpdir(), "agent-skill-hooks-"));
    const skillFolder = join(folder, "skills", "review");
    const ledger = join(folder, "order.txt");
    await mkdir(skillFolder, { recursive: true });
    await mkdir(join(folder, "agents"));
    await writeFile(join(folder, "agents", "reviewer.md"), [
      "---", "meta:", "  name: reviewer", "tools: [counter, bash]", "model: claude-sonnet-5", "---",
      "Use the supplied counter to record the review.",
    ].join("\n"));
    const hooks = Object.fromEntries([
      ["PreToolUse", "before"], ["PostToolUse", "after"], ["Stop", "stop"],
    ].map(([event, marker]) => [event, [{ ...(event === "Stop" ? {} : { matcher: "counter" }), hooks: [{
      type: "command", command: `printf '${marker}\\n' >> '${ledger}'`, timeout: 5,
    }] }]]));
    await writeFile(join(skillFolder, "SKILL.md"), [
      "---", "name: review", "description: Review with a named agent.", "context: fork",
      "agent: reviewer", `hooks: ${JSON.stringify(hooks)}`, "---",
      scripted([{ tool: { name: "counter", arguments: { value: "review" } } }, { text: "Child complete" }]),
    ].join("\n"));
    const callbacks: number[] = [];
    const agent = await createAgent({
      ...selection, skills: [folder],
      approvals: async (request) => ({ decision: denyHook && request.name === "bash" ? "deny" : "allow" }),
      tools: [{ name: "counter", description: "Record the review.", inputSchema: schema,
        handler: async (arguments_) => {
          assert.deepEqual(arguments_, { value: "review" });
          callbacks.push(process.pid);
          await appendFile(ledger, "counter\n");
          return "Recorded";
        } }],
    });
    try {
      const events = await run(agent, [
        { tool: { name: "load_skill", arguments: { name: "review" } } }, { text: "Parent complete" },
      ]);
      const result = terminal(events);
      assert.equal(result.state, denyHook ? "rejected" : "success");
      if (denyHook) {
        assert.equal(result.error?.code, "approval_denied");
        await assert.rejects(readFile(ledger));
        assert.deepEqual(callbacks, []);
      } else {
        assert.deepEqual(callbacks, [process.pid]);
        assert.equal(await readFile(ledger, "utf8"), "before\ncounter\nafter\nstop\n");
        assert.equal(events.filter((event) => event.type === "tool_call").length, 5);
        assert.ok(result.usage?.entries.some((entry) => (entry.tokens_in ?? 0n) > 0n));
      }
      const later = terminal(await run(agent, [{ text: "Independent turn" }]));
      assert.equal(later.state, "success");
      if (!denyHook) assert.equal(await readFile(ledger, "utf8"), "before\ncounter\nafter\nstop\n");
    } finally { await agent.close(); await rm(folder, { recursive: true, force: true }); }
  });
}

function terminal(events: Event[]): TurnResult {
  assert.equal(events[0]?.type, "turn_started");
  assert.equal(events.filter((event) => event.type === "terminal").length, 1);
  const last = events.at(-1);
  assert.ok(last?.type === "terminal");
  events.forEach((event, index) => assert.equal(event.sequence, BigInt(index + 1)));
  const calls = events.flatMap((event) => event.type === "tool_call" ? [event.payload.call.call_id] : []);
  const results = events.flatMap((event) => event.type === "tool_result" ? [event.payload.resolution.call_id] : []);
  assert.equal(new Set(calls).size, calls.length);
  assert.deepEqual([...results].sort(), [...calls].sort());
  const requests = events.flatMap((event) => event.type === "approval_request" ? [event.payload.request.request_id] : []);
  const decisions = events.flatMap((event) => event.type === "approval_decision" ? [event.payload.resolution.request_id] : []);
  assert.deepEqual([...decisions].sort(), [...requests].sort());
  assert.deepEqual(last.payload.content ?? [], events.flatMap((event) => event.type === "output_delta" ? event.payload.content : []));
  return last.payload;
}

const mcpServer = `
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";
const reply = (id, result, error) => process.stdout.write(JSON.stringify({jsonrpc:"2.0", id, ...(error ? {error} : {result})}) + "\\n");
createInterface({input:process.stdin}).on("line", line => {
  const request = JSON.parse(line);
  if (request.id === undefined) return;
  if (request.method === "initialize") return reply(request.id, {protocolVersion:request.params.protocolVersion, capabilities:{tools:{}}, serverInfo:{name:"ledger",version:"1"}});
  if (request.method === "tools/list") return reply(request.id, {tools:["record","fail","uncertain"].map(name => ({name, description:name, inputSchema:{type:"object",properties:{value:{type:"string"}},required:name === "fail" ? [] : ["value"]}}))});
  if (request.method === "tools/call") {
    const {name,arguments:args} = request.params;
    if (name === "fail") return reply(request.id, {isError:true,content:[{type:"text",text:"Ledger rejected the operation."}]});
    appendFileSync(process.env.MCP_LEDGER, JSON.stringify({pid:process.pid,value:args.value}) + "\\n");
    if (name === "uncertain") process.exit(17);
    return reply(request.id,{content:[{type:"text",text:args.value}],isError:false});
  }
  reply(request.id, undefined, {code:-32601,message:"Method not found"});
});
`;

for (const executor of ["built-in", "mcp"] as const) {
  for (const decision of ["allow", "deny", "cancel", "invalid", "timeout", "unavailable"] as const) {
    test(`${executor} approval ${decision} preserves effect authority`, { timeout: 20_000 }, async () => {
      const folder = await mkdtemp(join(tmpdir(), "agent-effects-"));
      const ledger = join(folder, "effects.jsonl");
      const service = join(folder, "mcp.mjs");
      await writeFile(service, mcpServer);
      const options: AgentOptions = { ...selection };
      if (decision === "allow" || decision === "deny") options.approvals = decision;
      else if (decision !== "unavailable") options.approvals = async () => {
        await assert.rejects(readFile(ledger));
        if (decision === "timeout") { await delay(450); return { decision: "allow" }; }
        if (decision === "invalid") return { decision: "allow", reason: 3 } as unknown as ApprovalResponse;
        return { decision: "cancel" };
      };
      if (executor === "mcp") options.mcpServers = [{ name: "ledger", transport: "stdio", command: process.execPath, args: [service], env: { MCP_LEDGER: ledger } }];
      const agent = await createAgent(options);
      try {
        const name = executor === "mcp" ? "mcp_ledger_record" : "bash";
        const args = executor === "mcp" ? { value: "once" } : { command: `printf effect > '${ledger}'` };
        const events = await run(agent, [{ tool: { name, arguments: args } }, { text: "Done" }]);
        terminal(events);
        assertApprovalOutcome(events, decision);
        if (decision !== "allow") {
          await assert.rejects(readFile(ledger));
        } else {
          const content = await readFile(ledger, "utf8");
          if (executor === "mcp") {
            const rows = content.trim().split("\n").map((line) => JSON.parse(line) as {pid:number;value:string});
            assert.equal(rows.length, 1);
            assert.notEqual(rows[0]?.pid, process.pid);
            assert.equal(rows[0]?.value, "once");
          } else assert.equal(content, "effect");
        }
        const call = events.find((event) => event.type === "tool_call");
        assert.ok(call?.type === "tool_call");
        assert.equal(call.payload.call.source, executor);
      } finally { await agent.close(); await rm(folder, { recursive: true, force: true }); }
    });
  }
}

test("MCP connection loss preserves one uncertain effect without retry", { timeout: 20_000 }, async () => {
  const folder = await mkdtemp(join(tmpdir(), "agent-mcp-loss-"));
  const ledger = join(folder, "effects.jsonl");
  const service = join(folder, "mcp.mjs");
  await writeFile(service, mcpServer);
  const agent = await createAgent({ ...selection, approvals: "allow", mcpServers: [{
    name: "ledger", transport: "stdio", command: process.execPath, args: [service], env: { MCP_LEDGER: ledger },
  }] });
  try {
    const events = await run(agent, [{ tool: { name: "mcp_ledger_uncertain", arguments: { value: "once" } } }]);
    assert.equal(terminal(events).error?.code, "tool_completion_unknown");
    assert.equal((await readFile(ledger, "utf8")).trim().split("\n").length, 1);
    const result = events.find((event) => event.type === "tool_result");
    assert.ok(result?.type === "tool_result");
    assert.equal(result.payload.resolution.outcome, "unknown");
  } finally { await agent.close(); await rm(folder, { recursive: true, force: true }); }
});

test("delegated caller work runs in Node and cancellation drains nested pairs", { timeout: 20_000 }, async () => {
  let enter!: () => void;
  let release!: () => void;
  const entered = new Promise<void>((resolve) => { enter = resolve; });
  const released = new Promise<void>((resolve) => { release = resolve; });
  const callbackPids: number[] = [];
  const agent = await createAgent({ ...selection, approvals: "allow", tools: [{
    name: "counter", description: "Record a caller effect.", inputSchema: schema,
    handler: async () => { callbackPids.push(process.pid); enter(); await released; return "counted"; },
  }] });
  try {
    const child = scripted([{ tool: { name: "counter", arguments: {} } }, { text: "Child complete" }]);
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn({ content: [{ type: "text", text: scripted([
      { tool: { name: "delegate", arguments: { instruction: child, tools: ["counter"] } } }, { text: "Parent complete" },
    ]) }] });
    const collecting = eventsOf(turn);
    await entered;
    const cancelling = turn.cancel();
    await delay(20);
    release();
    await cancelling;
    const events = await collecting;
    assert.equal(terminal(events).state, "cancelled");
    assert.deepEqual(callbackPids, [process.pid]);
    assert.equal(events.filter((event) => event.type === "tool_call").length, 2);
    await session.close();
  } finally { release(); await agent.close(); }
});

async function runtimePid(before: Set<string>): Promise<number> {
  const children = (await readFile(`/proc/${process.pid}/task/${process.pid}/children`, "utf8")).trim().split(/\s+/).filter(Boolean);
  for (const pid of children) {
    if (!before.has(pid) && (await readFile(`/proc/${pid}/cmdline`, "utf8")).includes("amplifier-agent-engine")) return Number(pid);
  }
  throw new Error("Agent construction did not start an identifiable runtime process.");
}

async function childIds(): Promise<Set<string>> {
  return new Set((await readFile(`/proc/${process.pid}/task/${process.pid}/children`, "utf8")).trim().split(/\s+/));
}

for (const cancel of [false, true]) {
  test(`engine loss drains reasoning and caller outcomes${cancel ? " after accepted cancellation" : ""}`, { timeout: 20_000 }, async () => {
    const before = await childIds();
    let enter!: () => void;
    let release!: () => void;
    const entered = new Promise<void>((resolve) => { enter = resolve; });
    const released = new Promise<void>((resolve) => { release = resolve; });
    const agent = await createAgent({ ...selection, approvals: "allow", tools: [{
      name: "counter", description: "Complete one caller effect.", inputSchema: schema,
      handler: async () => { enter(); await released; return "authoritative"; },
    }] });
    const pid = await runtimePid(before);
    let killed = false;
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const turn = await session.startTurn({ content: [{ type: "text", text: scripted([{
        events: [{ type: "llm:stream_block_delta", data: { block_type: "thinking", text: "Considering the effect." } }],
        tool: { name: "counter", arguments: {} },
      }]) }] });
      const events: Event[] = [];
      const collecting = (async () => { for await (const event of turn.events()) events.push(event); })();
      await entered;
      const cancelling = cancel ? turn.cancel().catch((error: unknown) => error) : undefined;
      if (cancel) await delay(30);
      process.kill(pid, "SIGKILL"); killed = true;
      await delay(30);
      assert.equal(events.some((event) => event.type === "terminal"), false);
      release();
      await cancelling;
      await collecting;
      const result = terminal(events);
      assert.equal(result.state, cancel ? "cancelled" : "failure");
      assert.equal(result.error?.code, cancel ? "turn_cancelled" : "engine_unavailable");
      const resolution = events.find((event) => event.type === "tool_result");
      assert.ok(resolution?.type === "tool_result");
      assert.equal(resolution.payload.resolution.outcome, "completed");
      assert.equal(resolution.payload.resolution.content, "authoritative");
      const reasoning = events.filter((event) => event.type === "reasoning_delta").map((event) => event.payload.text).join("");
      assert.equal(events.find((event) => event.type === "reasoning_final")?.payload.text, reasoning);
      assert.equal(events.at(-2)?.type, "usage");
    } finally {
      release();
      if (killed) await assert.rejects(agent.close(), (error: unknown) => error instanceof AgentError && error.code === "engine_unavailable");
      else await agent.close();
    }
  });
}
