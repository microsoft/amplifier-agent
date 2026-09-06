import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { AgentError, createAgent, ToolOutcomeUnknown } from "@microsoft/amplifier-agent";
import type { AgentOptions, Event, ToolHandler, Turn, TurnInput, TurnResult } from "@microsoft/amplifier-agent";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };
const schema = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };
type Step = Record<string, unknown>;
type Request = { messages: Array<{ role: string; content: unknown; tool_call_id?: string }> };
const text = (steps: Step[]) => "conformance-script:" + JSON.stringify(steps);
const input = (steps: Step[]): TurnInput => ({ content: [{ type: "text", text: text(steps) }] });
const call = (name: string, args: Record<string, unknown> = {}): Step => ({ tool: { name, arguments: args } });
const tool = (name: string, handler: ToolHandler) => ({ name, description: "Observe one effect.", inputSchema: schema, handler });
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };

function named(error: unknown, code: string): asserts error is AgentError {
  assert.ok(error instanceof AgentError, "Failure is a native full error record");
  assert.equal(error.code, code, "Error classification is preserved");
  assert.equal(error.retryable, false, "Unchanged failed effects are not safe retries");
  for (const key of ["message", "remedy", "category"] as const) assert.ok(error[key].length, `Error preserves ${key}`);
}

function observed(events: Event[]): TurnResult {
  assert.equal(events[0]?.type, "turn_started", "Admission precedes effects");
  assert.equal(events.filter(event => event.type === "terminal").length, 1, "Exactly one terminal");
  const terminal = events.at(-1);
  assert.ok(terminal?.type === "terminal", "Terminal is final");
  const calls = events.flatMap(event => event.type === "tool_call" ? [event.payload.call.call_id] : []);
  const results = events.flatMap(event => event.type === "tool_result" ? [event.payload.resolution.call_id] : []);
  assert.equal(new Set(calls).size, calls.length, "Calls have unique ids");
  assert.deepEqual([...results].sort(), [...calls].sort(), "Every concurrent call settles exactly once");
  const approvals = events.flatMap(event => event.type === "approval_request" ? [event.payload.request.request_id] : []);
  const decisions = events.flatMap(event => event.type === "approval_decision" ? [event.payload.resolution.request_id] : []);
  assert.deepEqual([...decisions].sort(), [...approvals].sort(), "Every approval settles once");
  for (const [index, event] of events.entries()) assert.equal(event.sequence, BigInt(index + 1), "Event order is contiguous");
  for (const id of calls) assert.ok(events.findIndex(event => event.type === "tool_call" && event.payload.call.call_id === id)
    < events.findIndex(event => event.type === "tool_result" && event.payload.resolution.call_id === id), "Effects cannot resolve before the visible call");
  return terminal.payload;
}
async function collect(turn: Turn, inspect?: (event: Event) => void): Promise<Event[]> {
  const events: Event[] = [];
  for await (const event of turn.events()) { events.push(event); inspect?.(event); }
  observed(events);
  return events;
}
async function fixture(run: (folder: string, requests: () => Promise<Request[]>) => Promise<void>) {
  const folder = await mkdtemp(join(tmpdir(), "agent-recovery-"));
  const log = join(folder, "requests.jsonl");
  const previous = process.env.CONFORMANCE_PROVIDER_REQUEST_LOG;
  process.env.CONFORMANCE_PROVIDER_REQUEST_LOG = log;
  await writeFile(log, "");
  try { await run(folder, async () => (await readFile(log, "utf8")).trim().split("\n").filter(Boolean).map(line => JSON.parse(line) as Request)); }
  finally {
    if (previous === undefined) delete process.env.CONFORMANCE_PROVIDER_REQUEST_LOG; else process.env.CONFORMANCE_PROVIDER_REQUEST_LOG = previous;
    await rm(folder, { recursive: true, force: true });
  }
}

for (const sibling of ["waiting", "running", "both"] as const) {
  test(`recovery: uncertainty drains ${sibling} concurrent siblings without new authority`, { timeout: 20_000 }, async () => {
    await fixture(async (_folder, requests) => {
      const waiting = deferred(), running = deferred(), release = deferred();
      let waitingEffects = 0, completed = false, uncertainCalls = 0;
      const agent = await createAgent({ ...selection, toolErrorPolicy: "continue", approvals: async request => {
        if (request.name === "waiting") { waiting.resolve(); await release.promise; }
        return { decision: "allow" };
      }, tools: [
        tool("uncertain", async () => {
          uncertainCalls++;
          if (sibling !== "waiting") await running.promise;
          if (sibling !== "running") await waiting.promise;
          throw new ToolOutcomeUnknown("The first executor cannot confirm its effect.");
        }),
        tool("running", async () => { running.resolve(); await release.promise; completed = true; return "Authoritative completion"; }),
        tool("waiting", async () => { waitingEffects++; return "Forbidden late effect"; }),
      ] });
      try {
        const session = await agent.createSession({ persistence: "ephemeral" });
        const names = ["uncertain", ...(sibling !== "waiting" ? ["running"] : []), ...(sibling !== "running" ? ["waiting"] : [])];
        const turn = await session.startTurn(input([{ tools: names.map(name => ({ name, arguments: {} })) }, { text: "Outcomes recorded." }]));
        const events = await collect(turn, event => {
          if (event.type === "tool_result" && event.payload.resolution.outcome === "unknown") {
            assert.equal(completed, false, "Admitted sibling stays in flight until its real result");
            release.resolve();
          }
        });
        const results = events.flatMap(event => event.type === "tool_result" ? [event.payload.resolution] : []);
        assert.equal(uncertainCalls, 1, "Uncertain work is not retried");
        assert.equal(waitingEffects, 0, "Late approval cannot admit new effects");
        assert.equal(completed, sibling !== "waiting", "Already executing effects drain normally");
        assert.deepEqual(results.map(result => result.outcome).sort(), names.map(name => name === "uncertain" ? "unknown" : name === "running" ? "completed" : "cancelled").sort());
        if (sibling !== "waiting") assert.equal(results.find(result => result.outcome === "completed")?.content, "Authoritative completion");
        const result = observed(events);
        assert.equal(result.state, sibling === "running" ? "success" : "failure");
        if (sibling !== "running") {
          named(result.error, "tool_recovery_blocked");
          const original = results.find(result => result.outcome === "unknown")!;
          const blocked = results.find(result => result.outcome === "cancelled")!;
          assert.deepEqual(blocked.error?.details, { uncertain_call_id: original.call_id });
        }
        assert.equal((await requests()).length, sibling === "running" ? 2 : 1, "Blocked authority cannot reach another completion");
      } finally { release.resolve(); await agent.close(); }
    });
  });
}

for (const policy of ["stop", "continue"] as const) {
  test(`recovery: a late settled callback cannot rewrite uncertainty under ${policy}`, { timeout: 20_000 }, async () => {
    await fixture(async (folder, requests) => {
      const previous = process.env.CONFORMANCE_CALLBACK_FAULT;
      const previousLog = process.env.CONFORMANCE_BOOTSTRAP_OBSERVATIONS;
      process.env.CONFORMANCE_CALLBACK_FAULT = "late_result";
      process.env.CONFORMANCE_BOOTSTRAP_OBSERVATIONS = join(folder, "late.log");
      let effects = 0;
      try {
        await using agent = await createAgent({ ...selection, approvals: "allow", toolErrorPolicy: policy,
          tools: [tool("effect", async () => { effects++; throw new ToolOutcomeUnknown("Inspect the external receipt."); })] });
        await using session = await agent.createSession({ persistence: "ephemeral" });
        const events = await collect(await session.startTurn(input([call("effect"), { text: "Unknown retained." }, { text: "Followup" }])));
        const result = observed(events);
        assert.equal(result.state, policy === "stop" ? "failure" : "success");
        const resolution = events.find(event => event.type === "tool_result")!.payload.resolution;
        assert.equal(resolution.outcome, "unknown");
        named(resolution.error, "tool_completion_unknown");
        const before = session.history;
        assert.equal((await session.run({ content: [{ type: "text", text: "Inspect the recorded result." }] })).state, "success");
        assert.match(await readFile(join(folder, "late.log"), "utf8"), /late-resolution/, "The engine received a late success after settlement");
        assert.deepEqual(session.history[0], before[0], "Late success cannot rewrite durable history");
        assert.equal(effects, 1, "No duplicate executor or retry");
        const message = (await requests()).at(-1)!.messages.find(message => message.role === "tool")!;
        const encoded = JSON.parse(String(message.content));
        const replay = "success" in encoded ? encoded.output : encoded;
        assert.equal(replay.outcome, "unknown");
        assert.equal(message.tool_call_id, resolution.call_id);
        assert.equal(replay.error.code, "tool_completion_unknown");
      } finally {
        if (previous === undefined) delete process.env.CONFORMANCE_CALLBACK_FAULT; else process.env.CONFORMANCE_CALLBACK_FAULT = previous;
        if (previousLog === undefined) delete process.env.CONFORMANCE_BOOTSTRAP_OBSERVATIONS; else process.env.CONFORMANCE_BOOTSTRAP_OBSERVATIONS = previousLog;
      }
    });
  });
}

test("recovery: accepted cancellation keeps a later unknown callback terminal", { timeout: 20_000 }, async () => {
  await fixture(async (_folder, requests) => {
    const entered = deferred(), release = deferred();
    let effects = 0;
    const agent = await createAgent({ ...selection, approvals: "allow", toolErrorPolicy: "continue",
      tools: [tool("effect", async () => { effects++; entered.resolve(); await release.promise; throw new ToolOutcomeUnknown("Inspect the cancelled effect."); })] });
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const turn = await session.startTurn(input([call("effect"), { text: "Forbidden recovery." }]));
      const events = collect(turn);
      await entered.promise;
      const cancelling = turn.cancel();
      release.resolve();
      await cancelling;
      const result = observed(await events);
      assert.equal(result.state, "cancelled");
      named(result.error, "turn_cancelled");
      assert.equal(effects, 1);
      assert.equal((await requests()).length, 1, "Cancellation cannot trigger model recovery");
    } finally { release.resolve(); await agent.close(); }
  });
});

for (const [kind, command] of [["failure", "exit 7"], ["block", "printf '%s' '{\"decision\":\"block\"}'"], ["invalid", "printf '{invalid'"], ["timeout", "sleep 5"]] as const) {
  test(`recovery: continue preserves terminal skill guard ${kind}`, { timeout: 20_000 }, async () => {
    await fixture(async (folder, requests) => {
      const skill = join(folder, "guard");
      await mkdir(skill);
      await writeFile(join(skill, "SKILL.md"), "---\n" + JSON.stringify({ name: "guard", description: "Guard effects.", hooks: {
        PreToolUse: [{ matcher: "effect", hooks: [{ type: "command", command, timeout: 1 }] }],
      } }) + "\n---\nGuard the requested effect.\n");
      let effects = 0;
      await using agent = await createAgent({ ...selection, skills: [folder], approvals: "allow", toolErrorPolicy: "continue",
        tools: [tool("effect", async () => { effects++; return "Forbidden effect"; })] });
      await using session = await agent.createSession({ persistence: "ephemeral" });
      const events = await collect(await session.startTurn(input([call("load_skill", { name: "guard" }), call("effect"), { text: "Forbidden recovery." }])));
      const result = observed(events);
      assert.equal(result.state, "failure");
      named(result.error, kind === "timeout" ? "tool_completion_unknown" : "tool_failed");
      assert.equal(effects, 0, "A failed or rejecting guard never admits the caller effect");
      assert.equal((await requests()).length, 2, "Guard failure cannot return to the model");
    });
  });
}

test("recovery: local inspection cannot bypass an active skill guard", { timeout: 20_000 }, async () => {
  await fixture(async (folder, requests) => {
    const skill = join(folder, "guard"), forbidden = join(folder, "guard-effect");
    await mkdir(skill);
    await writeFile(join(folder, "receipt"), "Prior receipt");
    await writeFile(join(skill, "SKILL.md"), "---\n" + JSON.stringify({ name: "guard", description: "Guard inspection.", hooks: {
      PreToolUse: [{ matcher: "read_file", hooks: [{ type: "command", command: `printf forbidden > '${forbidden}'` }] }],
    } }) + "\n---\nGuard file inspection.\n");
    await using agent = await createAgent({ ...selection, skills: [folder], approvals: "allow", toolErrorPolicy: "continue",
      tools: [tool("effect", async () => { throw new ToolOutcomeUnknown("Inspect the prior receipt."); })] });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const events = await collect(await session.startTurn(input([call("load_skill", { name: "guard" }), call("effect"), call("read_file", { file_path: join(folder, "receipt") })])));
    named(observed(events).error, "tool_recovery_blocked");
    assert.equal(events.filter(event => event.type === "tool_result").at(-1)?.payload.resolution.outcome, "cancelled");
    await assert.rejects(readFile(forbidden), "A guard command is itself an effect and cannot run after uncertainty");
    assert.equal((await requests()).length, 3);
  });
});

const mcpSource = `
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";
const reply = (id, result) => process.stdout.write(JSON.stringify({jsonrpc:"2.0", id, result}) + "\\n");
createInterface({input:process.stdin}).on("line", line => {
  const request = JSON.parse(line);
  if (request.id === undefined) return;
  if (request.method === "initialize") return reply(request.id,{protocolVersion:request.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:"recovery",version:"1"}});
  if (request.method === "tools/list") return reply(request.id,{tools:["failed","unknown","record"].map(name=>({name,description:"Report an executor outcome.",inputSchema:{type:"object"}}))});
  if (request.method === "tools/call") {
    appendFileSync(process.env.MCP_LEDGER, request.params.name + "\\n");
    if (request.params.name === "unknown") process.exit(17);
    return reply(request.id,{content:[{type:"text",text:"partial receipt"}],isError:request.params.name === "failed"});
  }
  reply(request.id,{});
});
`;

for (const source of ["built-in", "mcp"] as const) for (const failure of ["failed", "unknown"] as const) for (const policy of ["stop", "continue"] as const) {
  test(`recovery: ${source} ${failure} retains captured output under ${policy}`, { timeout: 20_000 }, async () => {
    await fixture(async (folder, requests) => {
      const server = join(folder, "mcp.mjs"), ledger = join(folder, "effects"), late = join(folder, "late");
      await writeFile(server, mcpSource);
      await using agent = await createAgent({ ...selection, approvals: "allow", toolErrorPolicy: policy,
        mcpServers: [{ name: "receipt", transport: "stdio", command: process.execPath, args: [server], env: { MCP_LEDGER: ledger } }] });
      await using session = await agent.createSession({ persistence: "ephemeral" });
      const attempt = source === "mcp" ? call(`mcp_receipt_${failure}`) : call("bash", {
        command: `printf partial-out; printf partial-err >&2; printf begun > '${ledger}'; ${failure === "failed" ? "exit 7" : `sleep 5; printf late > '${late}'`}`,
        timeout: 1,
      });
      const events = await collect(await session.startTurn(input([attempt, { text: "The executor outcome remains unchanged." }])));
      const result = observed(events);
      assert.equal(result.state, policy === "continue" ? "success" : "failure");
      const resolution = events.find(event => event.type === "tool_result")!.payload.resolution;
      assert.equal(resolution.outcome, failure);
      named(resolution.error, failure === "failed" ? "tool_failed" : "tool_completion_unknown");
      assert.equal(resolution.error.correlation_id, resolution.call_id);
      assert.equal((await readFile(ledger, "utf8")).trim(), source === "mcp" ? failure : "begun", "The executor runs once");
      if (source === "built-in") {
        const captured = JSON.parse(resolution.content!);
        assert.equal(captured.stdout, "partial-out"); assert.equal(captured.stderr, "partial-err");
        assert.notEqual(captured.returncode, 0);
        await assert.rejects(readFile(late), "Timed-out process is drained before terminal");
      } else if (failure === "failed") assert.match(resolution.content!, /partial receipt/);
      const sent = await requests();
      assert.equal(sent.length, policy === "continue" ? 2 : 1);
      if (policy === "continue") {
        const message = sent[1]!.messages.find(message => message.role === "tool")!;
        const encoded = JSON.parse(String(message.content));
        const replay = "success" in encoded ? encoded.output : encoded;
        assert.equal(message.tool_call_id, resolution.call_id);
        assert.equal(replay.outcome, failure);
        const completeError = Object.fromEntries(Object.entries(resolution.error).filter(([key]) => key !== "name"));
        completeError.message = resolution.error.message;
        completeError.details ??= null;
        completeError.correlation_id ??= null;
        assert.deepEqual(replay.error, completeError);
        if ("success" in encoded) { assert.equal(encoded.success, false); assert.deepEqual(encoded.error, completeError); }
        assert.equal(replay.content ?? undefined, resolution.content);
      }
    });
  });
}

test("recovery: uncertainty blocks a configured MCP executor before it starts", { timeout: 20_000 }, async () => {
  await fixture(async (folder, requests) => {
    const server = join(folder, "mcp.mjs"), ledger = join(folder, "effects");
    await writeFile(server, mcpSource);
    let effects = 0;
    await using agent = await createAgent({ ...selection, approvals: "allow", toolErrorPolicy: "continue",
      mcpServers: [{ name: "receipt", transport: "stdio", command: process.execPath, args: [server], env: { MCP_LEDGER: ledger } }],
      tools: [tool("effect", async () => { effects++; throw new ToolOutcomeUnknown("Inspect the effect."); })] });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const events = await collect(await session.startTurn(input([call("effect"), call("mcp_receipt_record")])));
    const result = observed(events);
    named(result.error, "tool_recovery_blocked");
    const resolutions = events.flatMap(event => event.type === "tool_result" ? [event.payload.resolution] : []);
    assert.deepEqual(resolutions.map(resolution => resolution.outcome), ["unknown", "cancelled"]);
    assert.deepEqual(resolutions[1]!.error?.details, { uncertain_call_id: resolutions[0]!.call_id });
    assert.match(result.error.remedy, /inspect/i); assert.match(result.error.remedy, /new turn/i);
    assert.equal(effects, 1);
    await assert.rejects(readFile(ledger), "The MCP executor receives no call");
    assert.equal((await requests()).length, 2);
  });
});

test("recovery: delegated uncertainty restricts subsequent root effects", { timeout: 20_000 }, async () => {
  await fixture(async (folder, requests) => {
    const forbidden = join(folder, "root-effect");
    let effects = 0;
    await using agent = await createAgent({ ...selection, approvals: "allow", toolErrorPolicy: "continue",
      tools: [tool("effect", async () => { effects++; throw new ToolOutcomeUnknown("Inspect the delegated effect."); })] });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const child = text([call("effect"), { text: "The delegated result remains unknown." }]);
    const events = await collect(await session.startTurn(input([
      call("delegate", { instruction: child, tools: ["effect"] }), call("bash", { command: `printf forbidden > '${forbidden}'` }),
    ])));
    named(observed(events).error, "tool_recovery_blocked");
    const results = events.flatMap(event => event.type === "tool_result" ? [event.payload.resolution] : []);
    const uncertain = results.find(result => result.outcome === "unknown")!;
    const blocked = results.find(result => result.error?.code === "tool_recovery_blocked")!;
    assert.deepEqual(blocked.error?.details, { uncertain_call_id: uncertain.call_id });
    assert.equal(effects, 1);
    await assert.rejects(readFile(forbidden), "A completed delegate cannot erase its nested uncertainty");
    assert.equal((await requests()).length, 4, "Only the restricted root request follows delegated recovery");
  });
});

test("recovery: a started delegate drains its nested effect after root uncertainty", { timeout: 20_000 }, async () => {
  await fixture(async (_folder, requests) => {
    const running = deferred(), waiting = deferred(), release = deferred();
    let completed = false, forbidden = 0;
    const agent = await createAgent({ ...selection, toolErrorPolicy: "continue", approvals: async request => {
      if (request.name === "waiting") { waiting.resolve(); await release.promise; }
      return { decision: "allow" };
    }, tools: [
      tool("uncertain", async () => { await running.promise; await waiting.promise; throw new ToolOutcomeUnknown("Inspect the root effect."); }),
      tool("running", async () => { running.resolve(); await release.promise; completed = true; return "Nested completion"; }),
      tool("waiting", async () => { forbidden++; return "Forbidden effect"; }),
    ] });
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const instruction = text([call("running"), { text: "Nested work completed." }]);
      const events = await collect(await session.startTurn(input([{ tools: [
        { name: "delegate", arguments: { instruction, tools: ["running"] } }, { name: "uncertain" }, { name: "waiting" },
      ] }])), event => {
        if (event.type === "tool_result" && event.payload.resolution.outcome === "unknown") release.resolve();
      });
      named(observed(events).error, "tool_recovery_blocked");
      assert.equal(completed, true, "The nested executor settles before terminal");
      assert.equal(forbidden, 0, "Pending authority never admits another effect");
      const calls = Object.fromEntries(events.flatMap(event => event.type === "tool_call" ? [[event.payload.call.name, event.payload.call.call_id]] : []));
      const results = Object.fromEntries(events.flatMap(event => event.type === "tool_result" ? [[event.payload.resolution.call_id, event.payload.resolution]] : []));
      assert.equal(results[calls.running!]!.outcome, "completed");
      assert.equal(results[calls.running!]!.content, "Nested completion");
      assert.equal(results[calls.delegate!]!.outcome, "completed");
      assert.equal(results[calls.uncertain!]!.outcome, "unknown");
      assert.equal(results[calls.waiting!]!.outcome, "cancelled");
      assert.equal((await requests()).length, 3, "Only the admitted delegate may finish its model response");
    } finally { release.resolve(); await agent.close(); }
  });
});

test("recovery: remedies repair invalid requests and retryability describes unchanged requests", { timeout: 20_000 }, async () => {
  await fixture(async (_folder, requests) => {
    for (const field of ["modle", "__class__", "__dict__"]) for (let attempt = 0; attempt < 2; attempt++) {
      await assert.rejects(createAgent({ ...selection, [field]: "claude-sonnet-5" } as AgentOptions), error => {
        named(error, "invalid_input");
        if (field === "modle") assert.equal(error.remedy, "Use model instead.", "The remedy names the usable replacement key");
        else assert.ok(error.message.includes(field), "Unknown magic names reach ordinary field validation");
        return true;
      });
    }
    assert.deepEqual(await requests(), [], "An unchanged invalid configuration never begins provider work");
    await using agent = await createAgent({ ...selection });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    for (let attempt = 0; attempt < 2; attempt++) {
      await assert.rejects(session.startTurn({ content: [] }), error => {
        named(error, "invalid_input");
        assert.equal(error.remedy, "Provide content or at least one history message.");
        return true;
      });
    }
    assert.deepEqual(await requests(), [], "An unchanged invalid turn is never retried implicitly");
    const sameRequest = input([{ failure: true, failure_retryable: true }, { text: "The same request succeeded." }]);
    const first = await session.run(sameRequest);
    assert.equal(first.state, "failure");
    assert.ok(first.error instanceof AgentError, "Retryability is carried by the full error");
    assert.equal(first.error.code, "provider_failed");
    assert.equal(first.error.retryable, true, "Transient provider failure allows an unchanged caller retry");
    assert.match(first.error.remedy, /availability/, "Provider remedy identifies the condition to inspect");
    assert.equal((await requests()).length, 1, "Retryable does not authorize an automatic retry");
    assert.equal((await session.run(sameRequest)).state, "success", "The identical public request may succeed");
    assert.equal((await requests()).length, 2, "Only the explicit caller retry reaches the provider");
  });
});
