import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";
import type {
  AgentOptions,
  ApprovalRequest,
  ApprovalResponse,
  Event,
  Tool,
  ToolContext,
  TurnInput,
  TurnResult,
} from "@microsoft/amplifier-agent";
import { AgentError, createAgent, ToolFailed } from "@microsoft/amplifier-agent";
import { parse, stringify } from "lossless-json";
import { assertApprovalOutcome } from "./approval.js";
import { collect, named, trace } from "./trace.js";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };
const input: TurnInput = { content: [{ type: "text", text: "Say hello" }] };
const schema = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };

interface RecordFixture {
  at: string;
  deadline: string;
  envelope_extension: unknown;
  payload_extension: unknown;
  progress: unknown;
  provider: Record<string, unknown>[];
  expected_cost: string;
  method_error_marker: string;
  terminal_error_marker: string;
  error: Record<string, unknown>;
  continuation_input: TurnInput;
}

interface ObservedRequest {
  messages: { role: string; content: string | { type: string; text?: string }[] }[];
  tools: { name: string; description: string; parameters: Record<string, unknown> }[];
}

function messageText(message: ObservedRequest["messages"][number]): string {
  return typeof message.content === "string"
    ? message.content
    : message.content.map((part) => part.text ?? "").join("");
}

async function recordFixture(): Promise<RecordFixture> {
  return parse(
    await readFile(new URL("../../../tests/support/scenarios/records.json", import.meta.url), "utf8"),
    undefined,
    (value) => {
      if (/^-?\d+$/.test(value) && !Number.isSafeInteger(Number(value))) return BigInt(value);
      return Number(value);
    },
  ) as RecordFixture;
}

function recordInput(script: unknown): TurnInput {
  return { content: [{ type: "text", text: `scripted:${stringify(script)}` }] };
}

function firstPrimary(events: Event[]): { provider: string; model: string } {
  const started = events[0];
  assert.ok(started?.type === "turn_started");
  return started.payload.primary_actual;
}

test("contract: session identity boundaries and lifecycle errors are method failures", {
  timeout: 30_000,
}, async () => {
  const storage = await mkdtemp(join(tmpdir(), "agent-contract-identities-"));
  const agent = await createAgent({ ...selection, storage });
  try {
    await assert.rejects(agent.createSession({ sessionId: "UPPER-id" }), named("session_id_invalid"));
    for (const sessionId of ["a1234567", "a".repeat(64)]) {
      const session = await agent.createSession({ sessionId });
      assert.deepEqual(session.info, { session_id: sessionId, persistence: "durable" });
      await assert.rejects(agent.createSession({ sessionId }), named("already_exists"));
      await assert.rejects(agent.resumeSession(sessionId), named("session_in_use"));
      await assert.rejects(agent.deleteSession(sessionId), named("session_in_use"));
      await session.close();
      const resumed = await agent.resumeSession(sessionId);
      assert.deepEqual(resumed.history, []);
      await resumed.close();
      await agent.deleteSession(sessionId);
      await assert.rejects(agent.resumeSession(sessionId), named("not_found"));
      await assert.rejects(agent.deleteSession(sessionId), named("not_found"));
    }
    const generated = await agent.createSession({ persistence: "ephemeral" });
    assert.match(generated.info.session_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const id = generated.info.session_id;
    await generated.close();
    await generated.close();
    await assert.rejects(agent.resumeSession(id), named("not_found"));
    await assert.rejects(generated.run(input), named("closed"));
    await assert.rejects(generated.startTurn({ ...input, history: [] }), named("closed"));
    await assert.rejects(generated.fork(), named("closed"));
    assert.throws(() => generated.info, named("closed"));
    assert.throws(() => generated.history, named("closed"));
    assert.deepEqual(await agent.listSessions(), []);
  } finally {
    await agent.close();
    await rm(storage, { recursive: true, force: true });
  }
  await agent.close();
  await assert.rejects(agent.createSession(), named("closed"));
  await assert.rejects(agent.resumeSession("a1234567"), named("closed"));
  await assert.rejects(agent.listSessions(), named("closed"));
  await assert.rejects(agent.deleteSession("a1234567"), named("closed"));
});

test("contract: busy turns refuse atomically while independent sessions make progress", {
  timeout: 20_000,
}, async () => {
  await using agent = await createAgent(selection);
  await using first = await agent.createSession({ persistence: "ephemeral" });
  await using second = await agent.createSession({ persistence: "ephemeral" });
  const turn = await first.startTurn({ content: [{ type: "text", text: "Wait for cancellation" }] });
  assert.deepEqual(turn.info, { session_id: first.info.session_id, turn_id: turn.info.turn_id });
  assert.ok(turn.info.turn_id.length > 0);
  assert.ok(Object.isFrozen(turn.info));
  assert.equal(Reflect.set(turn.info, "turn_id", "changed"), false);
  assert.ok(Object.isFrozen(first.info));
  assert.equal(Reflect.set(first.info, "session_id", "changed"), false);
  const stream = turn.events()[Symbol.asyncIterator]();
  assert.throws(() => turn.events(), named("stream_already_consumed"));
  const events: Event[] = [];
  while (true) {
    const item = await stream.next();
    assert.equal(item.done, false);
    events.push(item.value);
    if (item.value.type === "output_delta") break;
  }
  await assert.rejects(first.startTurn(input), named("busy"));
  await assert.rejects(first.startTurn({ ...input, history: [] }), named("busy"));
  await assert.rejects(first.run(input), named("busy"));
  await assert.rejects(first.fork(), named("busy"));
  assert.equal(first.history.length, 0);
  const independent = await second.run(input);
  assert.equal(independent.state, "success");
  assert.equal(first.history.length, 0);
  await turn.cancel();
  await turn.cancel();
  while (true) {
    const item = await stream.next();
    if (item.done) break;
    events.push(item.value);
  }
  const result = trace(events);
  assert.equal(result.state, "cancelled");
  named("turn_cancelled")(result.error);
  assert.equal(first.history.length, 1);
  assert.deepEqual(first.history[0]?.result, result);
  assert.equal((await first.run(input)).state, "success");
  assert.equal(first.history.length, 2);
  assert.equal(second.history.length, 1);
});

test("contract: exact usage and owned extensions cross the public binding losslessly", {
  timeout: 20_000,
}, async () => {
  const large = 9007199254740993n;
  const observed: unknown[] = [];
  const contexts: string[] = [];
  await using agent = await createAgent({
    ...selection,
    approvals: "allow",
    tools: [
      {
        name: "counter",
        description: "Observe an exact integer.",
        inputSchema: schema,
        handler: async (args, context) => {
          observed.push(args);
          contexts.push(context.call_id);
          return String(args.value);
        },
      },
    ],
  });
  await using session = await agent.createSession({ persistence: "ephemeral" });
  const script = JSON.stringify([
    {
      tool: { name: "counter", arguments: { value: "EXACT_INTEGER" } },
      usage: {
        input_tokens: "EXACT_INTEGER",
        output_tokens: 2,
        total_tokens: "EXACT_TOTAL",
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: "0.123456789012345678901234567890123",
      },
    },
    {
      events: [
        {
          type: "org.example.exact",
          data: {
            payload: { integer: "EXACT_INTEGER", parts: ["first", "second"] },
            "org.example.counter": "EXACT_TOTAL",
          },
        },
      ],
      chunks: ["Exact", " reply"],
      text: "Exact reply",
      usage: {
        input_tokens: 2,
        output_tokens: 3,
        total_tokens: 5,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: "0.000000000000000000000000000000002",
      },
    },
  ])
    .replaceAll('"EXACT_INTEGER"', String(large))
    .replaceAll('"EXACT_TOTAL"', String(large + 2n));
  const turn = await session.startTurn({ content: [{ type: "text", text: `scripted:${script}` }] });
  const events = await collect(turn);
  const result = trace(events);
  assert.equal(result.state, "success");
  assert.deepEqual(observed, [{ value: large }]);
  assert.equal(contexts.length, 1);
  assert.ok(contexts[0]);
  assert.deepEqual(result.usage?.entries, [
    {
      ...selection,
      tokens_in: large + 2n,
      tokens_out: 5n,
      cache_read_tokens: 0n,
      cache_write_tokens: 0n,
      cost: { USD: "0.123456789012345678901234567890125" },
    },
  ]);
  const extension = events.filter((event) => event.type === "org.example.exact");
  assert.equal(extension.length, 1);
  assert.deepEqual(extension[0]?.payload, { integer: large, parts: ["first", "second"] });
  assert.equal((extension[0] as unknown as Record<string, unknown>)["org.example.counter"], large + 2n);
  assert.deepEqual(session.history[0]?.result, result);
  const eventTypes = events.map((event) => event.type);
  const finalUsage = eventTypes.lastIndexOf("usage");
  assert.ok(finalUsage > eventTypes.lastIndexOf("output_delta"));
  assert.equal(finalUsage, events.length - 2);
});

test("contract: durable history survives independent Node hosts", { timeout: 20_000 }, async () => {
  const storage = await mkdtemp(join(tmpdir(), "agent-node-hosts-"));
  const script = `
    import { createAgent } from '@microsoft/amplifier-agent';
    const options = JSON.parse(process.argv[1]);
    const agent = await createAgent({provider:'anthropic', model:'claude-sonnet-5', storage:options.storage});
    const session = options.id ? await agent.resumeSession(options.id) : await agent.createSession();
    const before = session.history;
    const turn = await session.startTurn({content:[{type:'text',text:'Say hello'}]});
    const events = [];
    for await (const event of turn.events()) events.push(event);
    const output = {id:session.info.session_id, persistence:session.info.persistence, before, history:session.history, events};
    await agent.close();
    process.stdout.write(JSON.stringify(output, (_,value) => typeof value === 'bigint' ? String(value) : value));
  `;
  try {
    const execute = async (id?: string) => {
      const { stdout } = await promisify(execFile)(
        process.execPath,
        ["--input-type=module", "-e", script, JSON.stringify({ storage, ...(id === undefined ? {} : { id }) })],
        { timeout: 8_000 },
      );
      return JSON.parse(stdout) as {
        id: string;
        persistence: string;
        before: unknown[];
        history: unknown[];
        events: { type: string; payload: { continuation?: string; state?: string } }[];
      };
    };
    const first = await execute();
    assert.equal(first.persistence, "durable");
    assert.deepEqual(first.before, []);
    assert.equal(first.history.length, 1);
    assert.equal(first.events[0]?.payload.continuation, "fresh");
    const second = await execute(first.id);
    assert.equal(second.id, first.id);
    assert.deepEqual(second.before, first.history);
    assert.equal(second.history.length, 2);
    assert.deepEqual(second.history[0], first.history[0]);
    assert.equal(second.events[0]?.payload.continuation, "resumed");
    assert.equal(second.events.at(-1)?.payload.state, "success");
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

test("contract: evolved records and deadlines survive the binding unchanged", { timeout: 20_000 }, async () => {
  const fixture = await recordFixture();
  const callbacks: { arguments: unknown; context: ToolContext }[] = [];
  const approvals: ApprovalRequest[] = [];
  await using agent = await createAgent({
    ...selection,
    tools: [
      {
        name: "scripted_records",
        description: "Record exact values.",
        inputSchema: schema,
        handler: async (arguments_, context) => {
          callbacks.push({ arguments: arguments_, context });
          return "Recorded";
        },
      },
    ],
    approvals: async (request) => {
      approvals.push(request);
      return { decision: "allow" };
    },
  });
  await using session = await agent.createSession({ persistence: "ephemeral" });
  const turn = await session.startTurn(recordInput(fixture.provider));
  const events = await collect(turn);
  const result = trace(events);
  assert.equal(result.state, "success");
  for (const event of events) {
    assert.equal(event.at, fixture.at);
    assert.deepEqual((event as unknown as Record<string, unknown>)["org.example.envelope"], fixture.envelope_extension);
    if (event.type !== "org.example.terminal") {
      const payload = event.payload as Record<string, unknown>;
      assert.deepEqual(payload.future_optional, fixture.payload_extension);
      assert.deepEqual(payload["org.example.payload"], fixture.payload_extension);
    }
  }
  assert.equal(callbacks.length, 1);
  assert.equal(approvals.length, 1);
  const call = events.find((event) => event.type === "tool_call")!.payload.call;
  assert.equal(call.source, "caller");
  assert.equal(call.name, "scripted_records");
  assert.deepEqual(callbacks[0]?.arguments, { integer: 9007199254740993n, nested: [null, true, "\u03bb"] });
  assert.deepEqual(call.arguments, callbacks[0]?.arguments);
  assert.equal(callbacks[0]?.context.call_id, call.call_id);
  assert.equal(approvals[0]?.call_id, call.call_id);
  assert.equal(callbacks[0]?.context.deadline, fixture.deadline);
  assert.equal(call.deadline, fixture.deadline);
  assert.deepEqual(result.content, [
    { type: "text", text: "First" },
    { type: "text", text: "" },
    { type: "text", text: "Second" },
  ]);
  assert.deepEqual(result.usage?.entries, [
    {
      ...selection,
      tokens_in: 9007199254740995n,
      tokens_out: 5n,
      cache_read_tokens: 0n,
      cache_write_tokens: 0n,
      cost: { USD: fixture.expected_cost },
    },
  ]);
  assert.deepEqual(
    events.filter((event) => event.type === "reasoning_final").map((event) => event.payload.text),
    ["First thought"],
  );
  assert.deepEqual(
    events.filter((event) => event.type === "progress").map((event) => event.payload.data),
    [fixture.progress, fixture.progress],
  );
  const owned = events.find((event) => event.type === "org.example.terminal")!;
  assert.deepEqual(owned.payload, { marker: "scripted-records", state: "success", integer: 9007199254740993n });
  assert.equal((owned as unknown as Record<string, unknown>)["org.example.owned"], "Unchanged");
});

for (const phase of ["method", "terminal"] as const) {
  test(`contract: native ${phase} errors preserve the complete shared record`, { timeout: 20_000 }, async () => {
    const fixture = await recordFixture();
    const verifyError = (error: unknown) => {
      assert.ok(error instanceof AgentError, "Failure must preserve the public native error class.");
      const { name: _name, ...fields } = error;
      assert.deepEqual({ ...fields, message: error.message }, fixture.error);
      return true;
    };
    if (phase === "method") {
      await assert.rejects(createAgent({ ...selection, instructions: fixture.method_error_marker }), verifyError);
    } else {
      await using agent = await createAgent(selection);
      await using session = await agent.createSession({ persistence: "ephemeral" });
      const turn = await session.startTurn({ content: [{ type: "text", text: fixture.terminal_error_marker }] });
      const events = await collect(turn);
      const result = trace(events);
      assert.equal(result.state, "failure");
      verifyError(result.error);
    }
  });
}

test("contract: accepted options and callbacks are snapshotted per agent at construction", {
  timeout: 20_000,
}, async () => {
  const observed: unknown[] = [];
  const counter: Tool = {
    name: "counter",
    description: "Original description",
    inputSchema: { ...schema },
    handler: async (arguments_) => {
      observed.push(arguments_);
      return "Recorded";
    },
  };
  const refuser: Tool = {
    name: "refuser",
    description: "Refuse the effect.",
    inputSchema: schema,
    handler: async () => {
      throw new ToolFailed("Refused");
    },
  };
  const options: AgentOptions = {
    ...selection,
    instructions: "Original instructions",
    approvals: "allow",
    tools: [counter, refuser],
    skills: [],
    mcpServers: [],
  };
  await using agent = await createAgent(options);
  options.approvals = "deny";
  counter.handler = async (arguments_) => {
    observed.push({ denying: arguments_ });
    return "Denied agent executed";
  };
  await using denying = await createAgent(options);
  options.instructions = "Mutated instructions";
  options.model = "unregistered-model";
  options.toolErrorPolicy = "continue";
  counter.name = "mutated-tool";
  counter.description = "Mutated description";
  counter.inputSchema.type = "array";
  counter.handler = async () => {
    throw new Error("Mutated handler executed");
  };
  options.skills!.push("/nonexistent/skill-source");
  options.mcpServers!.push({ name: "mutated-server", transport: "stdio", command: "/nonexistent/mcp-command" });
  const script = recordInput([
    { observe_request: true, tool: { name: "counter", arguments: { value: "original" } } },
    { observe_request: true, text: "Recorded" },
  ]);
  for (let index = 0; index < 2; index++) {
    await using session = await agent.createSession({ persistence: "ephemeral" });
    await using denied = await denying.createSession({ persistence: "ephemeral" });
    const [events, deniedEvents] = await Promise.all([
      collect(await session.startTurn(script)),
      collect(await denied.startTurn({ content: [{ type: "text", text: "Call the counter" }] })),
    ]);
    assert.equal(trace(events).state, "success");
    const refusal = trace(deniedEvents);
    assert.equal(refusal.state, "rejected");
    named("approval_denied")(refusal.error);
    assert.deepEqual(firstPrimary(events), selection);
    const requests = events.filter((event) => event.type === "org.example.request");
    assert.equal(requests.length, 2);
    for (const event of requests) {
      const request = event.payload as ObservedRequest;
      assert.equal(messageText(request.messages[0]!), "Original instructions");
      const offered = request.tools.find((tool) => tool.name === "counter")!;
      assert.equal(offered.description, "Original description");
      assert.equal(offered.parameters.type, "object");
      assert.equal(
        request.tools.some((tool) => tool.name === "mutated-tool"),
        false,
      );
    }
  }
  assert.deepEqual(observed, [{ value: "original" }, { value: "original" }]);
  await using stopping = await agent.createSession({ persistence: "ephemeral" });
  const stopped = await stopping.run(
    recordInput([{ tool: { name: "refuser", arguments: {} } }, { text: "Unexpected continuation" }]),
  );
  assert.equal(stopped.state, "failure");
  named("tool_failed")(stopped.error);
});

test("contract: host configuration in the Node process environment reaches the engine", {
  timeout: 20_000,
}, async () => {
  const folder = await mkdtemp(join(tmpdir(), "agent-host-environment-"));
  const path = join(folder, "host.json");
  const keys = ["AMPLIFIER_AGENT_CONFIG", "AMPLIFIER_AGENT_MODEL", "AMPLIFIER_AGENT_MODLE"];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    for (const key of keys) delete process.env[key];
    await writeFile(path, "{}");
    process.env.AMPLIFIER_AGENT_CONFIG = path;
    process.env.AMPLIFIER_AGENT_MODEL = "claude-opus-5";
    await using agent = await createAgent({ provider: "anthropic" });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const events = await collect(await session.startTurn(input));
    assert.equal(trace(events).state, "success");
    assert.deepEqual(firstPrimary(events), { provider: "anthropic", model: "claude-opus-5" });
    delete process.env.AMPLIFIER_AGENT_MODEL;
    process.env.AMPLIFIER_AGENT_MODLE = "claude-sonnet-5";
    await assert.rejects(createAgent(selection), (error: unknown) => {
      named("invalid_input")(error);
      assert.match((error as AgentError).message, /AMPLIFIER_AGENT_MODLE/);
      return true;
    });
  } finally {
    for (const key of keys) {
      const value = previous[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(folder, { recursive: true, force: true });
  }
});

test("contract: an ambient deny policy in the Node process environment rejects a tool turn", {
  timeout: 20_000,
}, async () => {
  const folder = await mkdtemp(join(tmpdir(), "agent-host-approvals-"));
  const path = join(folder, "host.json");
  const keys = ["AMPLIFIER_AGENT_CONFIG", "AMPLIFIER_AGENT_APPROVALS"];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  const observed: unknown[] = [];
  const counter: Tool = {
    name: "counter",
    description: "Count.",
    inputSchema: schema,
    handler: async (arguments_) => {
      observed.push(arguments_);
      return "Counted";
    },
  };
  try {
    await writeFile(path, "{}");
    process.env.AMPLIFIER_AGENT_CONFIG = path;
    process.env.AMPLIFIER_AGENT_APPROVALS = "deny";
    await using agent = await createAgent({ ...selection, tools: [counter] });
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const result = await session.run(
      recordInput([{ tool: { name: "counter", arguments: {} } }, { text: "Unexpected continuation" }]),
    );
    assert.equal(result.state, "rejected");
    named("approval_denied")(result.error);
    assert.deepEqual(observed, []);
  } finally {
    for (const key of keys) {
      const value = previous[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(folder, { recursive: true, force: true });
  }
});

for (const mode of ["unavailable", "version_mismatch"] as const) {
  test(`contract: ${mode} startup fails before agent construction or provider work`, { timeout: 20_000 }, async () => {
    const folder = await mkdtemp(join(tmpdir(), "agent-bootstrap-"));
    const ledger = join(folder, "work.txt");
    const previousMode = process.env.SCRIPTED_STARTUP_MODE;
    const previousLedger = process.env.SCRIPTED_RUNTIME_LOG;
    process.env.SCRIPTED_STARTUP_MODE = mode;
    process.env.SCRIPTED_RUNTIME_LOG = ledger;
    try {
      await assert.rejects(
        async () => {
          const unexpected = await createAgent(selection);
          await unexpected.close();
        },
        (error: unknown) => {
          named(mode === "unavailable" ? "engine_unavailable" : "contract_version_mismatch")(error);
          assert.equal((error as AgentError).category, "lifecycle");
          assert.equal((error as AgentError).retryable, false);
          return true;
        },
      );
      await assert.rejects(readFile(ledger), { code: "ENOENT" });
    } finally {
      if (previousMode === undefined) delete process.env.SCRIPTED_STARTUP_MODE;
      else process.env.SCRIPTED_STARTUP_MODE = previousMode;
      if (previousLedger === undefined) delete process.env.SCRIPTED_RUNTIME_LOG;
      else process.env.SCRIPTED_RUNTIME_LOG = previousLedger;
      await rm(folder, { recursive: true, force: true });
    }
  });
}

test("contract: run resume and fork agree with the streamed terminal record", { timeout: 20_000 }, async () => {
  const fixture = await recordFixture();
  const storage = await mkdtemp(join(tmpdir(), "agent-binding-parity-"));
  try {
    const first = await createAgent({ ...selection, storage });
    let id: string;
    let result: TurnResult;
    try {
      const session = await first.createSession();
      id = session.info.session_id;
      result = await session.run(fixture.continuation_input);
      assert.equal(session.history.length, 1);
      assert.deepEqual(session.history[0]?.input, fixture.continuation_input);
      assert.deepEqual(session.history[0]?.result, result);
      await using streamed = await first.createSession({ persistence: "ephemeral" });
      const events = await collect(await streamed.startTurn(fixture.continuation_input));
      assert.deepEqual(trace(events), result, "run returns the complete streamed terminal record");
    } finally {
      await first.close();
    }
    await using second = await createAgent({ ...selection, storage });
    await using resumed = await second.resumeSession(id);
    const events = await collect(await resumed.startTurn(fixture.continuation_input));
    assert.deepEqual(trace(events), result);
    assert.ok(events[0]?.type === "turn_started");
    assert.equal(events[0].payload.continuation, "resumed");
    const parentHistory = resumed.history;
    await using child = await resumed.fork();
    assert.notEqual(child.info.session_id, resumed.info.session_id);
    assert.equal(child.info.persistence, resumed.info.persistence);
    assert.deepEqual(child.history, parentHistory);
    const childEvents = await collect(await child.startTurn(fixture.continuation_input));
    assert.deepEqual(trace(childEvents), result);
    assert.ok(childEvents[0]?.type === "turn_started");
    assert.equal(childEvents[0].payload.continuation, "resumed");
    assert.equal(child.history.length, parentHistory.length + 1);
    assert.deepEqual(resumed.history, parentHistory);
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

for (const decision of ["cancel", "invalid", "timeout", "unavailable"] as const) {
  test(`contract: caller approval ${decision} resolves once before effects`, { timeout: 20_000 }, async () => {
    let effects = 0;
    let effectsAtApproval: number | undefined;
    const options: AgentOptions = {
      ...selection,
      tools: [
        {
          name: "counter",
          description: "Count an approved effect.",
          inputSchema: schema,
          handler: async () => {
            effects++;
            return "1";
          },
        },
      ],
    };
    if (decision !== "unavailable")
      options.approvals = async () => {
        effectsAtApproval = effects;
        if (decision === "timeout") {
          await delay(450);
          return { decision: "allow" };
        }
        if (decision === "invalid") return { decision: "unregistered" } as unknown as ApprovalResponse;
        return { decision: "cancel" };
      };
    await using agent = await createAgent(options);
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn({ content: [{ type: "text", text: "Call the counter" }] });
    const events = await collect(turn);
    trace(events);
    assertApprovalOutcome(events, decision);
    assert.equal(effectsAtApproval, decision === "unavailable" ? undefined : 0);
    assert.equal(effects, 0);
    if (decision === "timeout") {
      await delay(300);
      assert.equal(effects, 0);
    }
  });
}
