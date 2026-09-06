import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { AgentError, createAgent, ToolFailed, ToolOutcomeUnknown } from "@microsoft/amplifier-agent";
import type { AgentOptions, Event, TurnInput, TurnResult } from "@microsoft/amplifier-agent";

interface Scenario {
  id: string;
  input: TurnInput;
  approvals?: "allow" | "deny" | "handler";
  tool?: string;
  tool_error?: "tool_failed" | "tool_completion_unknown";
  tool_error_policy?: "stop" | "continue";
  cancel_after?: string;
  expected: { state: string; text?: string; deltas?: string[]; effects: number; callbacks: number; approvals?: number; code?: string; outcomes?: string[]; tool_codes?: string[] };
}
const scenarios: Scenario[] = JSON.parse(await readFile(
  process.env.CONFORMANCE_SCENARIOS ?? new URL("../../../conformance/scenarios/turns.json", import.meta.url), "utf8",
)) as Scenario[];
const model = { provider: "anthropic", model: "claude-sonnet-5" };

function assertError(error: unknown, code: string): boolean {
  assert.ok(error instanceof AgentError);
  assert.equal(error.code, code);
  assert.equal(typeof error.category, "string");
  assert.equal(typeof error.message, "string");
  assert.ok(error.remedy.length);
  assert.equal(typeof error.retryable, "boolean");
  return true;
}

for (const scenario of scenarios) {
  test(`shared scenario: ${scenario.id}`, { timeout: 20_000 }, async () => {
    let effects = 0;
    let callbacks = 0;
    let approvals = 0;
    const callbackPids: number[] = [];
    const options: AgentOptions = { ...model };
    if (scenario.tool_error_policy) options.toolErrorPolicy = scenario.tool_error_policy;
    if (scenario.tool) options.tools = [{
      name: scenario.tool, description: "Count one completed call.",
      inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object", properties: { value: { type: "integer" } }, required: ["value"] },
      handler: async (args, context) => {
        callbacks++;
        callbackPids.push(process.pid);
        assert.ok(context.call_id);
        assert.ok(Object.isFrozen(context));
        assert.equal(Reflect.set(context, "call_id", "changed"), false);
        assert.equal(args.value, 7);
        effects++;
        if (scenario.tool_error === "tool_failed") throw new ToolFailed("The counter rejected the operation.");
        if (scenario.tool_error === "tool_completion_unknown") throw new ToolOutcomeUnknown("The counter outcome cannot be established.");
        return String(args.value);
      },
    }];
    if (scenario.approvals === "handler") options.approvals = async (request) => {
      approvals++;
      assert.ok(request.request_id);
      assert.ok(Object.isFrozen(request));
      return { decision: "allow" };
    };
    else if (scenario.approvals) options.approvals = scenario.approvals;
    const agent = await createAgent(options);
    options.toolErrorPolicy = options.toolErrorPolicy === "continue" ? "stop" : "continue";
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const acceptedInput = structuredClone(scenario.input);
      const turn = await session.startTurn(acceptedInput);
      if (acceptedInput.content[0]) acceptedInput.content[0].text = "Mutated after acceptance";
      const events: Event[] = [];
      const deltas: string[] = [];
      let result: TurnResult | undefined;
      for await (const event of turn.events()) {
        events.push(event);
        assert.equal(event.sequence, BigInt(events.length));
        assert.equal(event.session_id, session.info.session_id);
        assert.equal(event.turn_id, turn.info.turn_id);
        if (event.type === "output_delta") deltas.push(...event.payload.content.map((part) => part.text));
        if (event.type === "terminal") result = event.payload;
        if (event.type === scenario.cancel_after) await turn.cancel();
      }
      assert.equal(events[0]?.type, "turn_started");
      assert.equal(events.at(-1)?.type, "terminal");
      assert.equal(events.filter((event) => event.type === "terminal").length, 1);
      assert.ok(result);
      assert.equal(result.state, scenario.expected.state);
      if (scenario.expected.code) assertError(result.error, scenario.expected.code);
      if (scenario.expected.text !== undefined) assert.equal(result.content?.map((part) => part.text).join(""), scenario.expected.text);
      if (scenario.expected.deltas) assert.deepEqual(deltas, scenario.expected.deltas);
      assert.deepEqual(events.flatMap((event) => event.type === "output_delta" ? event.payload.content : []), result.content ?? []);
      const calls = events.filter((event) => event.type === "tool_call").map((event) => event.payload.call.call_id);
      const resolutions = events.filter((event) => event.type === "tool_result").map((event) => event.payload.resolution.call_id);
      assert.deepEqual(resolutions, calls);
      if (scenario.expected.outcomes) {
        const results = events.filter((event) => event.type === "tool_result").map((event) => event.payload.resolution);
        assert.deepEqual(results.map((result) => result.outcome), scenario.expected.outcomes);
        assert.deepEqual(results.map((result) => result.error?.code), scenario.expected.tool_codes);
        for (const resolution of results) assertError(resolution.error, resolution.error!.code);
        if (scenario.expected.code === "tool_recovery_blocked") {
          assert.equal(results[1]?.error?.category, "executor");
          assert.equal(results[1]?.error?.retryable, false);
          assert.deepEqual(results[1]?.error?.details, { uncertain_call_id: results[0]?.call_id });
        }
      }
      const requests = events.filter((event) => event.type === "approval_request").map((event) => event.payload.request.request_id);
      const decisions = events.filter((event) => event.type === "approval_decision").map((event) => event.payload.resolution.request_id);
      assert.deepEqual(decisions, requests);
      if (result.usage) {
        const usage = events.filter((event) => event.type === "usage").at(-1);
        assert.ok(usage?.type === "usage");
        assert.deepEqual(result.usage, usage.payload.snapshot);
        for (const entry of result.usage.entries) if (entry.tokens_in !== undefined) assert.equal(typeof entry.tokens_in, "bigint");
      }
      assert.equal(effects, scenario.expected.effects);
      assert.equal(callbacks, scenario.expected.callbacks);
      assert.equal(approvals, scenario.expected.approvals ?? 0);
      assert.ok(callbackPids.every((pid) => pid === process.pid));
      assert.deepEqual(session.history[0]?.input, scenario.input);
      assert.deepEqual(session.history[0]?.result, result);
      assert.throws(() => turn.events(), (error) => assertError(error, "stream_already_consumed"));
      await session.close();
      await session.close();
      await assert.rejects(session.startTurn(scenario.input), (error) => assertError(error, "closed"));
    } finally { await agent.close(); await agent.close(); }
  });
}

test("a paused event consumer does not block callbacks and close waits for the actual callback", { timeout: 20_000 }, async () => {
  let enter!: () => void;
  let release!: () => void;
  const entered = new Promise<void>((resolve) => { enter = resolve; });
  const released = new Promise<void>((resolve) => { release = resolve; });
  let effects = 0;
  const agent = await createAgent({ ...model, approvals: "allow", tools: [{
    name: "counter", description: "Perform a controlled effect.",
    inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" },
    handler: async () => { enter(); await released; effects++; return "7"; },
  }] });
  try {
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn({ content: [{ type: "text", text: "Call the counter" }] });
    await entered;
    let closed = false;
    const closing = agent.close().then(() => { closed = true; });
    await delay(25);
    assert.equal(closed, false);
    assert.equal(effects, 0);
    release();
    await closing;
    assert.equal(effects, 1);
    const events: Event[] = [];
    for await (const event of turn.events()) events.push(event);
    const terminal = events.at(-1);
    assert.ok(terminal?.type === "terminal");
    assert.equal(terminal.payload.state, "cancelled");
    assert.equal(events.filter((event) => event.type === "tool_result").length, 1);
  } finally { release(); await agent.close(); }
});

test("close waits for a pending approval and prevents its late allow from executing a tool", { timeout: 20_000 }, async () => {
  let enter!: () => void;
  let release!: () => void;
  const entered = new Promise<void>((resolve) => { enter = resolve; });
  const released = new Promise<void>((resolve) => { release = resolve; });
  let effects = 0;
  const agent = await createAgent({ ...model,
    approvals: async () => { enter(); await released; return { decision: "allow" }; },
    tools: [{ name: "counter", description: "Count approved effects.",
      inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" },
      handler: async () => { effects++; return "7"; },
    }],
  });
  try {
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn({ content: [{ type: "text", text: "Approve the counter" }] });
    await entered;
    let closed = false;
    const closing = agent.close().then(() => { closed = true; });
    await delay(25);
    assert.equal(closed, false);
    release();
    await closing;
    assert.equal(effects, 0);
    const events: Event[] = [];
    for await (const event of turn.events()) events.push(event);
    const terminal = events.at(-1);
    assert.ok(terminal?.type === "terminal");
    assert.equal(terminal.payload.state, "cancelled");
    assertError(terminal.payload.error, "turn_cancelled");
    assert.equal(events.filter((event) => event.type === "approval_request").length, 1);
    assert.equal(events.filter((event) => event.type === "approval_decision").length, 1);
  } finally { release(); await agent.close(); }
});

test("construction refuses malformed options through full named errors", { timeout: 20_000 }, async () => {
  for (const options of [
    null,
    { ...model, unknown_setting: true },
    { ...model, tools: {} },
    ...["retry", null, true, 1, [], {}].map((toolErrorPolicy) => ({ ...model, toolErrorPolicy })),
    { ...model, tools: [{ name: "missing-handler", description: "No callable handler", inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" } }] },
  ]) await assert.rejects(async () => {
    const unexpected = await createAgent(options as AgentOptions);
    await unexpected.close();
  }, (error) => assertError(error, "invalid_input"));
});

test("loss of a running engine produces one named failure terminal", { timeout: 20_000 }, async () => {
  const childIds = async () => (await readFile(`/proc/${process.pid}/task/${process.pid}/children`, "utf8")).trim().split(/\s+/).filter(Boolean);
  const before = new Set(await childIds());
  const agent = await createAgent(model);
  let killed = false;
  try {
    const candidates = (await childIds()).filter((pid) => !before.has(pid));
    const children = await Promise.all(candidates.map(async (pid) => ({ pid, command: await readFile(`/proc/${pid}/cmdline`, "utf8") })));
    const child = children.find(({ command }) => command.includes("amplifier-agent-engine"));
    assert.ok(child, "Agent construction must own its runtime process.");
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn({ content: [{ type: "text", text: "Wait for cancellation" }] });
    const events: Event[] = [];
    for await (const event of turn.events()) {
      events.push(event);
      if (event.type === "output_delta") { process.kill(Number(child.pid), "SIGKILL"); killed = true; }
    }
    assert.equal(events[0]?.type, "turn_started");
    assert.equal(events.filter((event) => event.type === "terminal").length, 1);
    const terminal = events.at(-1);
    assert.ok(terminal?.type === "terminal");
    assert.equal(terminal.payload.state, "failure");
    assertError(terminal.payload.error, "engine_unavailable");
    assert.deepEqual(terminal.payload.content, events.flatMap((event) => event.type === "output_delta" ? event.payload.content : []));
  } finally {
    if (killed) await assert.rejects(agent.close(), (error) => assertError(error, "engine_unavailable"));
    else await agent.close();
  }
});
