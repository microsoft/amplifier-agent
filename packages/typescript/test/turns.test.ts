import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import type { AgentOptions, Event, ToolHandler, TurnInput, TurnResult } from "@microsoft/amplifier-agent";
import {
  type AgentError,
  BUILTIN_TOOLS,
  createAgent,
  ToolFailed,
  ToolOutcomeUnknown,
} from "@microsoft/amplifier-agent";
import { collect, named, trace } from "./trace.js";

const model = { provider: "anthropic", model: "claude-sonnet-5" };
const schema = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };
const scripted = (steps: Record<string, unknown>[]): TurnInput => ({
  content: [{ type: "text", text: `scripted:${JSON.stringify(steps)}` }],
});
const counterCall = { tool: { name: "counter", arguments: { value: 7 } } };
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

interface Row {
  id: string;
  steps: Record<string, unknown>[];
  approvals?: "allow" | "handler";
  outcome?: () => string;
  cancelAfter?: Event["type"];
  state: TurnResult["state"];
  code?: string;
  text?: string;
  resolution?: { outcome: string; code: string };
}

const unexpected = { chunks: ["Unexpected continuation"], text: "Unexpected continuation" };
const rows: Row[] = [
  {
    id: "text streams deltas into the terminal content",
    steps: [{ chunks: ["Hello ", "world"], text: "Hello world" }],
    state: "success",
    text: "Hello world",
  },
  {
    id: "tool runs the caller handler in the host process",
    steps: [counterCall, { chunks: ["Counted"], text: "Counted" }],
    approvals: "allow",
    outcome: () => "7",
    state: "success",
    text: "Counted",
  },
  {
    id: "approval handler receives a frozen request",
    steps: [counterCall, { chunks: ["Approved"], text: "Approved" }],
    approvals: "handler",
    outcome: () => "7",
    state: "success",
    text: "Approved",
  },
  {
    id: "cancel from inside the event iterator",
    steps: [{ chunks: ["Waiting"], block: true }],
    cancelAfter: "output_delta",
    state: "cancelled",
    code: "turn_cancelled",
  },
  {
    id: "ToolFailed maps to tool_failed",
    steps: [counterCall, unexpected],
    approvals: "allow",
    outcome: () => {
      throw new ToolFailed("The counter rejected the operation.");
    },
    state: "failure",
    code: "tool_failed",
    resolution: { outcome: "failed", code: "tool_failed" },
  },
  {
    id: "ToolOutcomeUnknown maps to tool_completion_unknown",
    steps: [counterCall, unexpected],
    approvals: "allow",
    outcome: () => {
      throw new ToolOutcomeUnknown("The counter outcome cannot be established.");
    },
    state: "failure",
    code: "tool_completion_unknown",
    resolution: { outcome: "unknown", code: "tool_completion_unknown" },
  },
  {
    id: "a plain thrown Error maps to tool_callback_failed",
    steps: [counterCall, unexpected],
    approvals: "allow",
    outcome: () => {
      throw new Error("The executor stopped.");
    },
    state: "failure",
    code: "tool_callback_failed",
    resolution: { outcome: "unknown", code: "tool_callback_failed" },
  },
  {
    id: "a non-string handler result maps to tool_result_invalid",
    steps: [counterCall, unexpected],
    approvals: "allow",
    outcome: () => ({ duplicate: "result" }) as unknown as string,
    state: "failure",
    code: "tool_result_invalid",
    resolution: { outcome: "unknown", code: "tool_result_invalid" },
  },
];

for (const row of rows) {
  test(`turn: ${row.id}`, { timeout: 20_000 }, async () => {
    const handled: { args: unknown; callId: string; frozen: boolean; writable: boolean; pid: number }[] = [];
    const requests: { requestId: string; frozen: boolean }[] = [];
    const options: AgentOptions = { ...model };
    const outcome = row.outcome;
    if (outcome)
      options.tools = [
        {
          name: "counter",
          description: "Count one completed call.",
          inputSchema: {
            ...schema,
            properties: { value: { type: "integer" } },
            required: ["value"],
          },
          handler: async (args, context) => {
            handled.push({
              args,
              callId: context.call_id,
              frozen: Object.isFrozen(context),
              writable: Reflect.set(context, "call_id", "changed"),
              pid: process.pid,
            });
            return outcome();
          },
        },
      ];
    if (row.approvals === "handler")
      options.approvals = async (request) => {
        requests.push({ requestId: request.request_id, frozen: Object.isFrozen(request) });
        return { decision: "allow" };
      };
    else if (row.approvals) options.approvals = row.approvals;
    const agent = await createAgent(options);
    options.toolErrorPolicy = "continue";
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const input = scripted(row.steps);
      const acceptedInput = structuredClone(input);
      const turn = await session.startTurn(acceptedInput);
      acceptedInput.content[0]!.text = "Mutated after acceptance";
      const events: Event[] = [];
      for await (const event of turn.events()) {
        events.push(event);
        assert.equal(event.session_id, session.info.session_id);
        assert.equal(event.turn_id, turn.info.turn_id);
        if (event.type === row.cancelAfter) await turn.cancel();
      }
      const result = trace(events);
      assert.equal(result.state, row.state);
      if (row.code) named(row.code)(result.error);
      if (row.text !== undefined) assert.equal(result.content?.map((part) => part.text).join(""), row.text);
      assert.equal(handled.length, outcome ? 1 : 0);
      for (const call of handled) {
        assert.deepEqual(call.args, { value: 7 });
        assert.ok(call.callId);
        assert.equal(call.frozen, true);
        assert.equal(call.writable, false);
        assert.equal(call.pid, process.pid);
      }
      assert.equal(requests.length, row.approvals === "handler" ? 1 : 0);
      for (const request of requests) {
        assert.ok(request.requestId);
        assert.equal(request.frozen, true);
      }
      const resolutions = events.flatMap((event) => (event.type === "tool_result" ? [event.payload.resolution] : []));
      if (row.resolution) {
        assert.deepEqual(
          resolutions.map((resolution) => resolution.outcome),
          [row.resolution.outcome],
        );
        named(row.resolution.code)(resolutions[0]!.error);
        assert.equal(resolutions[0]!.error!.correlation_id, resolutions[0]!.call_id);
      }
      if (result.usage)
        for (const entry of result.usage.entries)
          if (entry.tokens_in !== undefined) assert.equal(typeof entry.tokens_in, "bigint");
      assert.deepEqual(session.history[0]?.input, input);
      assert.deepEqual(session.history[0]?.result, result);
      assert.throws(() => turn.events(), named("stream_already_consumed"));
      await session.close();
      await session.close();
      await assert.rejects(session.startTurn(input), named("closed"));
    } finally {
      await agent.close();
      await agent.close();
    }
  });
}

test("uncertainty drains concurrent running and waiting callbacks without new authority", {
  timeout: 20_000,
}, async () => {
  const waiting = deferred();
  const running = deferred();
  const release = deferred();
  let waitingEffects = 0;
  let uncertainCalls = 0;
  let completed = false;
  let completedAtUnknown: boolean | undefined;
  const tool = (name: string, handler: ToolHandler) => ({
    name,
    description: "Observe one effect.",
    inputSchema: schema,
    handler,
  });
  const agent = await createAgent({
    ...model,
    toolErrorPolicy: "continue",
    approvals: async (request) => {
      if (request.name === "waiting") {
        waiting.resolve();
        await release.promise;
      }
      return { decision: "allow" };
    },
    tools: [
      ...BUILTIN_TOOLS,
      tool("uncertain", async () => {
        uncertainCalls++;
        await running.promise;
        await waiting.promise;
        throw new ToolOutcomeUnknown("The first executor cannot confirm its effect.");
      }),
      tool("running", async () => {
        running.resolve();
        await release.promise;
        completed = true;
        return "Authoritative completion";
      }),
      tool("waiting", async () => {
        waitingEffects++;
        return "Forbidden late effect";
      }),
    ],
  });
  try {
    const session = await agent.createSession({ persistence: "ephemeral" });
    const names = ["uncertain", "running", "waiting"];
    const turn = await session.startTurn(
      scripted([{ tools: names.map((name) => ({ name, arguments: {} })) }, { text: "Outcomes recorded." }]),
    );
    const events: Event[] = [];
    for await (const event of turn.events()) {
      events.push(event);
      if (event.type === "tool_result" && event.payload.resolution.outcome === "unknown") {
        completedAtUnknown = completed;
        release.resolve();
      }
    }
    const result = trace(events);
    const results = events.flatMap((event) => (event.type === "tool_result" ? [event.payload.resolution] : []));
    assert.equal(completedAtUnknown, false, "The admitted sibling stays in flight until its real result");
    assert.equal(uncertainCalls, 1, "Uncertain work is not retried");
    assert.equal(waitingEffects, 0, "A late approval cannot admit a new effect");
    assert.equal(completed, true, "An already executing effect drains normally");
    assert.deepEqual(results.map((resolution) => resolution.outcome).sort(), ["cancelled", "completed", "unknown"]);
    assert.equal(results.find((resolution) => resolution.outcome === "completed")?.content, "Authoritative completion");
    assert.equal(result.state, "failure");
    named("tool_recovery_blocked")(result.error);
    const original = results.find((resolution) => resolution.outcome === "unknown")!;
    const blocked = results.find((resolution) => resolution.outcome === "cancelled")!;
    assert.deepEqual(blocked.error?.details, { uncertain_call_id: original.call_id });
  } finally {
    release.resolve();
    await agent.close();
  }
});

for (const closer of ["agent", "session"] as const) {
  test(`${closer} close waits for the actual callback while the event consumer is paused`, {
    timeout: 20_000,
  }, async () => {
    const entered = deferred();
    const released = deferred();
    let effects = 0;
    const agent = await createAgent({
      ...model,
      approvals: "allow",
      tools: [
        {
          name: "counter",
          description: "Perform a controlled effect.",
          inputSchema: schema,
          handler: async () => {
            entered.resolve();
            await released.promise;
            effects++;
            return "7";
          },
        },
      ],
    });
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const turn = await session.startTurn({ content: [{ type: "text", text: "Call the counter" }] });
      await entered.promise;
      let closed = false;
      const closing = (closer === "agent" ? agent.close() : session.close()).then(() => {
        closed = true;
      });
      await delay(25);
      assert.equal(closed, false);
      assert.equal(effects, 0);
      released.resolve();
      await closing;
      assert.equal(effects, 1);
      await session.close();
      const events = await collect(turn);
      const result = trace(events);
      assert.equal(result.state, "cancelled");
      named("turn_cancelled")(result.error);
      assert.equal(events.filter((event) => event.type === "tool_result").length, 1);
      assert.throws(() => turn.info, named("closed"));
    } finally {
      released.resolve();
      await agent.close();
    }
  });
}

test("close waits for a pending approval and prevents its late allow from executing a tool", {
  timeout: 20_000,
}, async () => {
  const entered = deferred();
  const released = deferred();
  let effects = 0;
  const agent = await createAgent({
    ...model,
    approvals: async () => {
      entered.resolve();
      await released.promise;
      return { decision: "allow" };
    },
    tools: [
      {
        name: "counter",
        description: "Count approved effects.",
        inputSchema: schema,
        handler: async () => {
          effects++;
          return "7";
        },
      },
    ],
  });
  try {
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn({ content: [{ type: "text", text: "Approve the counter" }] });
    await entered.promise;
    let closed = false;
    const closing = agent.close().then(() => {
      closed = true;
    });
    await delay(25);
    assert.equal(closed, false);
    released.resolve();
    await closing;
    assert.equal(effects, 0);
    const events = await collect(turn);
    const terminal = events.at(-1);
    assert.ok(terminal?.type === "terminal");
    assert.equal(terminal.payload.state, "cancelled");
    named("turn_cancelled")(terminal.payload.error);
    assert.equal(events.filter((event) => event.type === "approval_request").length, 1);
    assert.equal(events.filter((event) => event.type === "approval_decision").length, 1);
  } finally {
    released.resolve();
    await agent.close();
  }
});

test("construction refuses malformed options through full named errors", { timeout: 20_000 }, async () => {
  for (const options of [
    null,
    { ...model, unsupported: true },
    { ...model, tools: {} },
    { ...model, toolErrorPolicy: "retry" },
    {
      ...model,
      tools: [{ name: "missing-handler", description: "No callable handler", inputSchema: schema }],
    },
  ])
    await assert.rejects(
      async () => {
        const unexpected = await createAgent(options as AgentOptions);
        await unexpected.close();
      },
      (error) => {
        named("invalid_input")(error);
        if (options && "unsupported" in options) assert.match((error as AgentError).message, /unsupported/);
        return true;
      },
    );
});

test("options set to undefined behave as omitted", { timeout: 20_000 }, async () => {
  const agent = await createAgent({ ...model, instructions: undefined, approvals: undefined, tools: undefined });
  try {
    const session = await agent.createSession({ sessionId: undefined, persistence: "ephemeral", model: undefined });
    assert.match(session.info.session_id, /^[0-9a-f]{8}-/);
    const input = scripted([{ chunks: ["Done"], text: "Done" }]);
    const result = await session.run({ ...input, model: undefined, history: undefined });
    assert.equal(result.state, "success");
  } finally {
    await agent.close();
  }
});

test("delegated caller work runs in Node and cancellation drains nested pairs", { timeout: 20_000 }, async () => {
  const entered = deferred();
  const released = deferred();
  const callbackPids: number[] = [];
  const agent = await createAgent({
    ...model,
    approvals: "allow",
    tools: [
      ...BUILTIN_TOOLS,
      {
        name: "counter",
        description: "Record a caller effect.",
        inputSchema: schema,
        handler: async () => {
          callbackPids.push(process.pid);
          entered.resolve();
          await released.promise;
          return "counted";
        },
      },
    ],
  });
  try {
    const child = scripted([{ tool: { name: "counter", arguments: {} } }, { text: "Child complete" }]).content[0]!.text;
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn(
      scripted([
        { tool: { name: "delegate", arguments: { instruction: child, tools: ["counter"] } } },
        { text: "Parent complete" },
      ]),
    );
    const collecting = collect(turn);
    await entered.promise;
    const cancelling = turn.cancel();
    await delay(20);
    released.resolve();
    await cancelling;
    const events = await collecting;
    assert.equal(trace(events).state, "cancelled");
    assert.deepEqual(callbackPids, [process.pid]);
    assert.equal(events.filter((event) => event.type === "tool_call").length, 2);
    await session.close();
  } finally {
    released.resolve();
    await agent.close();
  }
});

async function childIds(): Promise<Set<string>> {
  return new Set((await readFile(`/proc/${process.pid}/task/${process.pid}/children`, "utf8")).trim().split(/\s+/));
}

async function runtimePid(before: Set<string>): Promise<number> {
  for (const pid of await childIds()) {
    if (!before.has(pid) && (await readFile(`/proc/${pid}/cmdline`, "utf8")).includes("amplifier-agent-engine"))
      return Number(pid);
  }
  throw new Error("Agent construction did not start an identifiable runtime process.");
}

for (const cancel of [false, true]) {
  test(`engine loss drains reasoning and caller outcomes${cancel ? " after accepted cancellation" : ""}`, {
    timeout: 20_000,
  }, async () => {
    const before = await childIds();
    const entered = deferred();
    const released = deferred();
    const agent = await createAgent({
      ...model,
      approvals: "allow",
      tools: [
        {
          name: "counter",
          description: "Complete one caller effect.",
          inputSchema: schema,
          handler: async () => {
            entered.resolve();
            await released.promise;
            return "authoritative";
          },
        },
      ],
    });
    const pid = await runtimePid(before);
    let killed = false;
    try {
      const session = await agent.createSession({ persistence: "ephemeral" });
      const turn = await session.startTurn(
        scripted([
          {
            events: [
              { type: "llm:stream_block_delta", data: { block_type: "thinking", text: "Considering the effect." } },
            ],
            tool: { name: "counter", arguments: {} },
          },
        ]),
      );
      const events: Event[] = [];
      const collecting = (async () => {
        for await (const event of turn.events()) events.push(event);
      })();
      await entered.promise;
      const cancelling = cancel ? turn.cancel().catch((error: unknown) => error) : undefined;
      if (cancel) await delay(30);
      process.kill(pid, "SIGKILL");
      killed = true;
      await delay(30);
      assert.equal(
        events.some((event) => event.type === "terminal"),
        false,
      );
      released.resolve();
      await cancelling;
      await collecting;
      const result = trace(events);
      assert.equal(result.state, cancel ? "cancelled" : "failure");
      named(cancel ? "turn_cancelled" : "engine_unavailable")(result.error);
      const resolution = events.find((event) => event.type === "tool_result");
      assert.ok(resolution?.type === "tool_result");
      assert.equal(resolution.payload.resolution.outcome, "completed");
      assert.equal(resolution.payload.resolution.content, "authoritative");
      const reasoning = events
        .filter((event) => event.type === "reasoning_delta")
        .map((event) => event.payload.text)
        .join("");
      assert.equal(events.find((event) => event.type === "reasoning_final")?.payload.text, reasoning);
      assert.equal(events.at(-2)?.type, "usage");
    } finally {
      released.resolve();
      if (killed) await assert.rejects(agent.close(), named("engine_unavailable"));
      else await agent.close();
    }
  });
}

test("engine loss during streamed output keeps that output in the synthesized terminal", {
  timeout: 20_000,
}, async () => {
  const before = await childIds();
  const agent = await createAgent(model);
  const pid = await runtimePid(before);
  let killed = false;
  try {
    const session = await agent.createSession({ persistence: "ephemeral" });
    const turn = await session.startTurn(scripted([{ chunks: ["Before ", "loss"], block: true }]));
    const events: Event[] = [];
    for await (const event of turn.events()) {
      events.push(event);
      if (event.type === "output_delta" && !killed) {
        process.kill(pid, "SIGKILL");
        killed = true;
      }
    }
    const result = trace(events);
    assert.equal(result.state, "failure");
    named("engine_unavailable")(result.error);
    assert.ok(result.content?.length, "The terminal carries the output streamed before the loss");
  } finally {
    if (killed) await assert.rejects(agent.close(), named("engine_unavailable"));
    else await agent.close();
  }
});
