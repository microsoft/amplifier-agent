import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { AgentError, createAgent, ToolFailed, ToolOutcomeUnknown } from "@microsoft/amplifier-agent";
import type { AgentOptions, Event, Session, ToolHandler, TurnInput, TurnResult } from "@microsoft/amplifier-agent";
import { assertApprovalOutcome } from "./approval.js";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };
const schema = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };
type Step = Record<string, unknown>;
interface Request { messages: Array<{ role: string; content: unknown; tool_call_id?: string }>; tools: Array<{ name: string; parameters: Record<string, unknown> }> }
const input = (steps: Step[]): TurnInput => ({ content: [{ type: "text", text: "conformance-script:" + JSON.stringify(steps) }] });
const call = (name: string, arguments_: Record<string, unknown> = {}): Step => ({ tool: { name, arguments: arguments_ } });
const mcpSource = `
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";
const reply = (id, result) => process.stdout.write(JSON.stringify({jsonrpc:"2.0", id, result}) + "\\n");
createInterface({input:process.stdin}).on("line", line => {
  const request = JSON.parse(line);
  if (request.id === undefined) return;
  if (request.method === "initialize") return reply(request.id, {protocolVersion:request.params.protocolVersion, capabilities:{tools:{}}, serverInfo:{name:"ledger",version:"1"}});
  if (request.method === "tools/list") return reply(request.id, {tools:[{name:"record",description:"Record an effect.",inputSchema:{type:"object",properties:{value:{type:"string"}},required:["value"]}}]});
  if (request.method === "tools/call") {
    appendFileSync(process.env.MCP_LEDGER, JSON.stringify({pid:process.pid,arguments:request.params.arguments}) + "\\n");
    return reply(request.id,{content:[{type:"text",text:"Recorded"}],isError:false});
  }
  reply(request.id, {});
});
`;

function named(error: unknown, code: string): asserts error is AgentError {
  assert.ok(error instanceof AgentError, "Failures retain the public error class");
  assert.equal(error.code, code, "The failure has the specified registered code");
  for (const key of ["category", "message", "remedy"] as const) assert.ok(error[key].trim().length > 0, `The failure retains ${key}`);
  assert.equal(error.retryable, false, "Effects are not automatically retryable");
}

function terminal(events: Event[]): TurnResult {
  assert.equal(events[0]?.type, "turn_started", "Admission precedes turn events");
  assert.equal(events.filter((event) => event.type === "terminal").length, 1, "Exactly one terminal event is published");
  const last = events.at(-1);
  assert.ok(last?.type === "terminal", "Terminal is the final event");
  for (const [index, event] of events.entries()) assert.equal(event.sequence, BigInt(index + 1), "Sequence is contiguous");
  const calls = events.flatMap((event) => event.type === "tool_call" ? [event.payload.call.call_id] : []);
  const results = events.flatMap((event) => event.type === "tool_result" ? [event.payload.resolution.call_id] : []);
  assert.equal(new Set(calls).size, calls.length, "Each call has a distinct identity");
  assert.deepEqual(results, calls, "Each serial call resolves once in order");
  for (const id of calls) assert.ok(events.findIndex((event) => event.type === "tool_call" && event.payload.call.call_id === id)
    < events.findIndex((event) => event.type === "tool_result" && event.payload.resolution.call_id === id), "Call precedes result");
  return last.payload;
}

async function collect(session: Session, steps: Step[]): Promise<Event[]> {
  const turn = await session.startTurn(input(steps));
  const events: Event[] = [];
  for await (const event of turn.events()) events.push(event);
  terminal(events);
  return events;
}

async function withLedger(run: (folder: string, requests: () => Promise<Request[]>) => Promise<void>): Promise<void> {
  const folder = await mkdtemp(join(tmpdir(), "agent-policy-"));
  const ledger = join(folder, "requests.jsonl");
  const previous = process.env.CONFORMANCE_PROVIDER_REQUEST_LOG;
  process.env.CONFORMANCE_PROVIDER_REQUEST_LOG = ledger;
  await writeFile(ledger, "");
  try { await run(folder, async () => (await readFile(ledger, "utf8")).trim().split("\n").filter(Boolean).map((line) => JSON.parse(line) as Request)); }
  finally {
    if (previous === undefined) delete process.env.CONFORMANCE_PROVIDER_REQUEST_LOG; else process.env.CONFORMANCE_PROVIDER_REQUEST_LOG = previous;
    await rm(folder, { recursive: true, force: true });
  }
}

test("policy: invalid history refuses before any provider or executor work", { timeout: 20_000 }, async () => {
  await withLedger(async (_folder, requests) => {
    let effects = 0;
    await using agent = await createAgent({ ...selection, approvals: "allow", tools: [{ name: "effect", description: "Record one effect.", inputSchema: schema,
      handler: async () => { effects++; return "done"; } }] });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    for (const history of [[{ role: "tool", content: [] }], [{ role: "user", content: [{ type: "image", url: "fixture" }] }]]) {
      await assert.rejects(session.startTurn({ ...input([call("effect")]), history } as unknown as TurnInput), (error: unknown) => {
        named(error, "invalid_input");
        assert.match(error.message + " " + error.remedy, /history/, "The remedy identifies the unsupported field");
        return true;
      });
      assert.deepEqual(await requests(), [], "Invalid input cannot reach the provider");
      assert.equal(effects, 0, "Invalid input cannot start an executor");
      assert.deepEqual(session.history, [], "Refusal cannot append a turn");
    }
    const accepted = await session.run({ ...input([{ text: "Accepted" }]), history: [{ role: "user", content: [{ type: "text", text: "Earlier" }] }] });
    assert.equal(accepted.state, "success", "Refusal preserves first-turn history eligibility");
    assert.equal((await requests()).length, 1, "Only the accepted turn reaches the provider");
  });
});

for (const failure of ["failed", "unknown", "callback", "invalid"] as const) {
  for (const policy of ["stop", "continue"] as const) {
    test(`policy: ${failure} callback under ${policy} preserves truthful resolution and model input`, { timeout: 20_000 }, async () => {
      await withLedger(async (_folder, requests) => {
        let effects = 0;
        const handler: ToolHandler = async () => {
          effects++;
          if (failure === "failed") throw new ToolFailed("The effect was refused by its executor.");
          if (failure === "unknown") throw new ToolOutcomeUnknown("The effect cannot be established.");
          if (failure === "callback") throw new Error("The executor stopped.");
          return { duplicate: "result" } as unknown as string;
        };
        await using agent = await createAgent({ ...selection, approvals: "allow", toolErrorPolicy: policy, tools: [{ name: "effect", description: "Perform one effect.", inputSchema: schema, handler }] });
        await using session = await agent.createSession({ persistence: "ephemeral" });
        const events = await collect(session, [call("effect"), { text: "The recorded outcome remains unchanged." }]);
        const result = terminal(events);
        const code = { failed: "tool_failed", unknown: "tool_completion_unknown", callback: "tool_callback_failed", invalid: "tool_result_invalid" }[failure];
        const recovered = policy === "continue" && (failure === "failed" || failure === "unknown");
        assert.equal(result.state, recovered ? "success" : "failure", "Only ordinary executor failures can continue");
        const resolutions = events.flatMap((event) => event.type === "tool_result" ? [event.payload.resolution] : []);
        assert.equal(resolutions.length, 1, "The effect has exactly one resolution");
        const resolution = resolutions[0]!;
        assert.equal(resolution.outcome, failure === "failed" ? "failed" : "unknown", "Resolution preserves uncertainty");
        named(resolution.error, code);
        assert.equal(resolution.error.correlation_id, resolution.call_id, "Error identifies its tool call");
        if (!recovered) named(result.error, code);
        assert.equal(effects, 1, "The effect is never retried");
        const sent = await requests();
        assert.equal(sent.length, recovered ? 2 : 1, "Terminal failures cannot request model continuation");
        assert.deepEqual(session.history[0]?.result, result, "Durable public history retains the terminal record");
        if (recovered) {
          const message = sent[1]!.messages.find((message) => message.role === "tool");
          assert.ok(message, "The continuing model receives the tool resolution");
          assert.equal(message.tool_call_id, resolution.call_id, "Model input preserves call correlation");
          const content = typeof message.content === "string" ? message.content : JSON.stringify(message.content);
          assert.ok(content.includes(code), "Model input retains the named failure");
          assert.ok(content.includes(resolution.outcome), "Model input retains the truthful outcome");
          if (failure === "unknown") assert.deepEqual(sent[1]!.tools.map((tool) => tool.name).sort(), ["glob", "grep", "read_file"], "Uncertainty offers only local inspection");
        }
      });
    });
  }
}

for (const attempt of ["caller", "shell", "write", "delegate", "skill"] as const) {
  test(`policy: uncertainty blocks ${attempt} effects until a new turn`, { timeout: 20_000 }, async () => {
    await withLedger(async (folder, requests) => {
      let effects = 0;
      let approvals = 0;
      const path = join(folder, "effect.txt");
      await mkdir(join(folder, "repeat"));
      await writeFile(join(folder, "repeat", "SKILL.md"), "---\nname: repeat\ndescription: Repeat a requested operation.\n---\nPerform the requested operation.\n");
      await using agent = await createAgent({ ...selection, skills: [folder], toolErrorPolicy: "continue", approvals: async () => { approvals++; return { decision: "allow" }; }, tools: [{
        name: "effect", description: "Perform an effect.", inputSchema: schema, handler: async () => { effects++; if (effects === 1) throw new ToolOutcomeUnknown("Inspect the original effect."); return "New work"; },
      }] });
      await using session = await agent.createSession({ persistence: "ephemeral" });
      const next = {
        caller: call("effect", { changed: true }), shell: call("bash", { command: `printf repeated > '${path}'` }),
        write: call("write_file", { file_path: path, content: "repeated" }), delegate: call("delegate", { instruction: "Repeat the effect" }),
        skill: call("load_skill", { name: "repeat" }),
      }[attempt];
      const events = await collect(session, [call("effect"), next]);
      const result = terminal(events);
      assert.equal(result.state, "failure", "Restricted effects terminate recovery");
      named(result.error, "tool_recovery_blocked");
      const resolutions = events.flatMap((event) => event.type === "tool_result" ? [event.payload.resolution] : []);
      assert.equal(resolutions.length, 2, "The blocked request receives its own resolution");
      assert.equal(resolutions[0]!.outcome, "unknown", "The original outcome stays unknown");
      assert.equal(resolutions[1]!.outcome, "cancelled", "The new effect never begins");
      named(resolutions[1]!.error, "tool_recovery_blocked");
      assert.deepEqual(resolutions[1]!.error.details, { uncertain_call_id: resolutions[0]!.call_id }, "The remedy identifies the original uncertain call");
      assert.equal(effects, 1, "No second executor is called");
      assert.equal(approvals, 1, "A blocked effect cannot obtain fresh authority");
      await assert.rejects(readFile(path), "No shell or write effect may begin");
      assert.equal((await requests()).length, 2, "The blocked request cannot return to the model");
      assert.equal((await session.run(input([call("effect"), { text: "New turn complete" }]))).state, "success", "A new turn restores ordinary authority");
      assert.equal(effects, 2, "Only the new turn executes the next effect");
      assert.equal(approvals, 2, "The new turn obtains normal approval");
    });
  });
}

for (const decision of ["allow", "deny"] as const) {
  test(`policy: uncertainty retains ${decision} authority over local inspection`, { timeout: 20_000 }, async () => {
    await withLedger(async (folder, requests) => {
      const path = join(folder, "receipt.txt");
      await writeFile(path, "Original effect receipt");
      const approvals: string[] = [];
      await using agent = await createAgent({ ...selection, toolErrorPolicy: "continue", approvals: async (request) => {
        assert.ok(request.name, "Inspection approval identifies its tool");
        approvals.push(request.name); return { decision: request.name === "read_file" ? decision : "allow" };
      }, tools: [{ name: "effect", description: "Attempt one effect.", inputSchema: schema, handler: async () => { throw new ToolOutcomeUnknown("Inspect the receipt."); } }] });
      await using session = await agent.createSession({ persistence: "ephemeral" });
      const events = await collect(session, [call("effect"), call("read_file", { file_path: path }), { text: "Explained the receipt." }]);
      const result = terminal(events);
      assert.deepEqual(approvals, ["effect", "read_file"], "Read-only recovery still requires approval");
      assert.equal(result.state, decision === "allow" ? "success" : "rejected", "Recovery cannot bypass an inspection refusal");
      if (decision === "deny") named(result.error, "approval_denied");
      assert.equal((await requests()).length, decision === "allow" ? 3 : 2, "Only approved inspection can return to the model");
    });
  });
}

test("policy: tool recovery configuration is validated and snapshotted before provider work", { timeout: 20_000 }, async () => {
  await withLedger(async (_folder, requests) => {
    for (const policy of ["retry", "", null, true, []]) {
      await assert.rejects(createAgent({ ...selection, toolErrorPolicy: policy } as unknown as AgentOptions), (error: unknown) => { named(error, "invalid_input"); return true; });
      assert.deepEqual(await requests(), [], "Invalid recovery configuration cannot reach the provider");
    }
    const options: AgentOptions = { ...selection, approvals: "allow", tools: [{ name: "effect", description: "Report a failure.", inputSchema: schema, handler: async () => { throw new ToolFailed("Refused"); } }] };
    await using agent = await createAgent(options);
    options.toolErrorPolicy = "continue";
    await using session = await agent.createSession({ persistence: "ephemeral" });
    named((await session.run(input([call("effect"), { text: "Unexpected continuation" }]))).error, "tool_failed");
    assert.equal((await requests()).length, 1, "Default stop is snapshotted before caller mutation");
  });
});

test("policy: tool result ceiling is validated and bounds what reaches the model", { timeout: 20_000 }, async () => {
  await withLedger(async (_folder, requests) => {
    for (const ceiling of [0, -1, "big", 1.5, true]) {
      await assert.rejects(createAgent({ ...selection, toolResultMaxBytes: ceiling } as unknown as AgentOptions), (error: unknown) => { named(error, "invalid_input"); return true; });
      assert.deepEqual(await requests(), [], "An invalid ceiling cannot reach the provider");
    }
    const report = "y".repeat(5_000);
    const options: AgentOptions = { ...selection, approvals: "allow", toolResultMaxBytes: 120, tools: [{ name: "reporter", description: "Return a sized report.", inputSchema: schema, handler: async () => report }] };
    await using agent = await createAgent(options);
    options.toolResultMaxBytes = 5_000;
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const events = await collect(session, [call("reporter"), { text: "Read" }]);
    assert.equal(terminal(events).state, "success");
    const resolution = events.flatMap((event) => event.type === "tool_result" ? [event.payload.resolution] : []).at(0);
    assert.ok(resolution);
    assert.equal(resolution.truncated, true, "A shortened resolution says so");
    assert.equal(resolution.original_bytes, 5_000, "A shortened resolution names the original size");
    assert.equal(resolution.content, "y".repeat(120) + "\n...[tool output reached limit: kept 120 of 5000 bytes]", "The kept prefix carries one marker line");
    const replayed = (await requests()).at(-1)?.messages.filter((message) => message.role === "tool") ?? [];
    assert.equal(replayed.length, 1, "The bounded result is replayed once");
    const replay = JSON.stringify(replayed[0]?.content);
    assert.match(replay, /kept 120 of 5000 bytes/, "The model reads the bounded result");
    assert.ok(!replay.includes("y".repeat(121)), "The bytes beyond the ceiling never reach the model");
  });
});

for (const source of ["caller", "built-in", "mcp"] as const) {
  for (const decision of ["allow", "deny"] as const) {
    test(`policy: flat ${source} tool is visible and vetoable before its sole executor runs with ${decision}`, { timeout: 20_000 }, async () => {
      await withLedger(async (folder, requests) => {
        const ledger = join(folder, "effects.jsonl");
        const server = join(folder, "mcp.mjs");
        await writeFile(server, mcpSource);
        let resolveApproval!: (value: { decision: "allow" | "deny" }) => void;
        const permission = new Promise<{ decision: "allow" | "deny" }>((resolve) => { resolveApproval = resolve; });
        const argument = { value: "ordered" };
        const declaration = { name: "caller_effect", description: "Record one host effect.", inputSchema: { ...schema, properties: { value: { type: "string" } }, required: ["value"] },
          safety: { readOnlyHint: true, approval: "unnecessary" }, handler: async (arguments_: Record<string, unknown>) => {
            assert.deepEqual(arguments_, argument, "Arguments are decoded strict JSON");
            await writeFile(ledger, JSON.stringify({ pid: process.pid, arguments: arguments_ }));
            return "Recorded";
          } };
        await using agent = await createAgent({ ...selection, tools: [declaration], approvals: async () => {
          await assert.rejects(readFile(ledger), "Descriptive safety metadata cannot authorize effects");
          return permission;
        }, mcpServers: [{ name: "ledger", transport: "stdio", command: process.execPath, args: [server], env: { MCP_LEDGER: ledger } }] });
        await using session = await agent.createSession({ persistence: "ephemeral" });
        const name = source === "caller" ? "caller_effect" : source === "built-in" ? "bash" : "mcp_ledger_record";
        const args = source === "built-in" ? { command: `printf '%s' '{"executor":"built-in"}' > '${ledger}'` } : argument;
        const events: Event[] = [];
        const turn = await session.startTurn(input([call(name, args), { text: "Finished" }]));
        for await (const event of turn.events()) {
          events.push(event);
          if (event.type === "tool_call") {
            assert.equal(event.payload.call.source, source, "The source identifies the executor before work");
            assert.equal(event.payload.call.name, name, "The visible call names the selected tool");
            await assert.rejects(readFile(ledger), "No executor may precede the visible call");
          }
          if (event.type === "approval_request") {
            assert.ok(events.some((prior) => prior.type === "tool_call"), "Approval follows the visible call");
            await assert.rejects(readFile(ledger), "No executor may precede authority");
            resolveApproval({ decision });
          }
        }
        const result = terminal(events);
        assertApprovalOutcome(events, decision);
        const sent = await requests();
        const tools = sent[0]!.tools;
        const names = tools.map((tool) => tool.name);
        assert.equal(new Set(names).size, names.length, "The provider receives one flat namespace");
        for (const expected of ["caller_effect", "bash", "mcp_ledger_record"]) assert.ok(names.includes(expected), `The flat namespace includes ${expected}`);
        const offered = tools.find((tool) => tool.name === declaration.name)!;
        assert.deepEqual(offered.parameters, declaration.inputSchema, "The caller schema reaches the provider unchanged");
        assert.equal(sent.length, decision === "allow" ? 2 : 1, "Only approved effects can continue to the model");
        if (decision === "deny") { named(result.error, "approval_denied"); await assert.rejects(readFile(ledger), "A denied tool produces no effect"); }
        else {
          const rows = (await readFile(ledger, "utf8")).trim().split("\n").map((line) => JSON.parse(line) as { pid?: number; arguments?: unknown; executor?: string });
          assert.equal(rows.length, 1, "Exactly one executor performs the effect");
          if (source === "caller") assert.equal(rows[0]!.pid, process.pid, "Caller code executes in the host process");
          if (source === "mcp") assert.notEqual(rows[0]!.pid, process.pid, "MCP code executes in its configured process");
          if (source === "built-in") assert.equal(rows[0]!.executor, "built-in", "The engine executes its built-in tool");
        }
      });
    });
  }
}
