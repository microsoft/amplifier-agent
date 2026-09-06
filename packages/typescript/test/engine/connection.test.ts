import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { test } from "node:test";
import type { AgentOptions, Event } from "../../src/records.js";
import { Callbacks } from "../../src/internal/callbacks.js";
import { decode, encode } from "../../src/internal/codec.js";

const { Connection } = await import(new URL("../../../engine/src/amplifier_agent_engine/_node_host/connection.mjs", import.meta.url).href);
interface TestConnection { turn(info: { session_id: string; turn_id: string }, history: () => void): { events(): AsyncIterable<Event> } }

const info = { session_id: "session-1", turn_id: "turn-1" };
const toolCall = { call_id: "call-1", name: "counter", source: "caller", arguments: {} };
const callback = { event: "callback", callback_id: "callback-1", turn_id: info.turn_id, kind: "tool",
  args: { name: "counter", arguments: {}, context: { call_id: "call-1" } } };

function fixture(handler: () => Promise<string>, window = Number.POSITIVE_INFINITY) {
  const child = Object.assign(new EventEmitter(), {
    stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    kill: () => true,
  });
  const options: AgentOptions = { tools: [{ name: "counter", description: "Count calls.",
    inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" }, handler }] };
  const outgoing: Array<Record<string, any>> = [];
  const pendingEvents: unknown[] = [];
  const flush = () => {
    while (pendingEvents.length && window > 0) {
      window--;
      child.stdout.write(`${JSON.stringify(pendingEvents.shift())}\n`);
    }
  };
  child.stdin.on("data", (line: Buffer) => {
    const frame = JSON.parse(line.toString());
    outgoing.push(frame);
    if (frame.method === "turn.ack") { window += frame.params.count; flush(); }
  });
  const callbacks = new Callbacks(options);
  const connection = new Connection(child, {
    encode, decode,
    dispatch: callbacks.dispatch.bind(callbacks), settled: callbacks.settled.bind(callbacks),
  }) as TestConnection;
  const send = (frame: unknown) => child.stdout.write(`${JSON.stringify(frame)}\n`);
  const sequences = new Map<string, number>();
  const event = (type: string, payload: unknown, identity = info) => {
    const sequence = (sequences.get(identity.turn_id) ?? 0) + 1;
    sequences.set(identity.turn_id, sequence);
    pendingEvents.push({ event: "turn_event", turn_id: identity.turn_id,
      event_data: { ...identity, contract_version: "turn-events/1", sequence, type, payload } });
    flush();
  };
  event("turn_started", { continuation: "fresh", primary_actual: { provider: "anthropic", model: "claude-sonnet-5" } });
  return { child, connection, send, event, outgoing };
}

test("an early callback waits for its visible call while the consumer is paused", async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return "counted"; });
  f.send(callback);
  assert.equal(calls, 0);
  const stream = f.connection.turn(info, () => {});
  assert.equal(calls, 0);
  f.event("output_delta", { content: [{ type: "text", text: "Working" }] });
  assert.equal(calls, 0);
  assert.ok(f.outgoing.filter((frame) => frame.method === "turn.ack").length >= 2);
  f.event("tool_call", { call: toolCall });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(f.outgoing.find((frame) => frame.method === "callback.resolve")?.params.result, "counted");
  f.event("tool_result", { resolution: { call_id: "call-1", outcome: "completed", content: "counted" } });
  f.event("terminal", { state: "success", content: [{ type: "text", text: "Working" }] });
  const events: Event[] = [];
  for await (const event of stream.events()) events.push(event);
  assert.deepEqual(events.map((event) => event.type), ["turn_started", "output_delta", "tool_call", "tool_result", "terminal"]);
  assert.equal(f.outgoing.filter((frame) => frame.method === "turn.ack").length, events.length);
  f.child.emit("close", 0, null);
});

for (const cancellation of ["cancel_accepted", "callback_cancel"] as const) {
  test(`queued callbacks drain without executing after ${cancellation}`, async () => {
    let calls = 0;
    const f = fixture(async () => { calls++; return "must not execute"; });
    const stream = f.connection.turn(info, () => {});
    f.send(callback);
    f.send({ event: cancellation, turn_id: info.turn_id, callback_id: "callback-1" });
    f.event("tool_call", { call: toolCall });
    await Promise.resolve();
    assert.equal(calls, 0);
    const reply = f.outgoing.find((frame) => frame.method === "callback.resolve")?.params;
    assert.equal(reply?.callback_id, "callback-1");
    assert.equal(reply?.call_id, "call-1");
    assert.equal(reply?.error.kind, "tool_not_executed");
    f.child.emit("close", null, "SIGKILL");
    const events: Event[] = [];
    for await (const event of stream.events()) events.push(event);
    assert.equal(events.at(-1)?.type, "terminal");
    assert.equal(events.find((event) => event.type === "tool_result")?.payload.resolution.outcome, "cancelled");
  });
}

test("losing the engine drains an unannounced callback without invoking it", async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return "must not execute"; });
  const stream = f.connection.turn(info, () => {});
  f.send(callback);
  f.child.emit("close", null, "SIGKILL");
  const events: Event[] = [];
  for await (const event of stream.events()) events.push(event);
  assert.equal(calls, 0);
  assert.equal(events.length, 2);
  assert.ok(events[1]?.type === "terminal");
  assert.equal(events[1].payload.error?.code, "engine_unavailable");
});

test("a callback beyond the event window progresses with a paused consumer", async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return "counted"; }, 64);
  const stream = f.connection.turn(info, () => {});
  for (let index = 0; index < 80; index++) f.event("output_delta", { content: [{ type: "text", text: "." }] });
  f.event("tool_call", { call: toolCall });
  assert.equal(calls, 0);
  f.send(callback);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls, 1);
  f.event("tool_result", { resolution: { call_id: "call-1", outcome: "completed", content: "counted" } });
  f.event("terminal", { state: "success" });
  const events: Event[] = [];
  for await (const event of stream.events()) events.push(event);
  assert.equal(events.length, 84);
  assert.equal(f.outgoing.filter((frame) => frame.method === "turn.ack").length, events.length);
  f.child.emit("close", 0, null);
});

test("simultaneous turns with equal call IDs keep callbacks and credit separate", async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return "second turn effect"; });
  const second = { session_id: "session-2", turn_id: "turn-2" };
  const firstStream = f.connection.turn(info, () => {});
  f.event("tool_call", { call: toolCall });
  f.event("turn_started", { continuation: "fresh", primary_actual: { provider: "anthropic", model: "claude-sonnet-5" } }, second);
  const secondStream = f.connection.turn(second, () => {});
  f.send({ ...callback, turn_id: second.turn_id });
  assert.equal(calls, 0);
  assert.deepEqual(f.outgoing.filter((frame) => frame.method === "turn.ack").map((frame) => frame.params.turn_id), [second.turn_id]);
  f.event("tool_call", { call: toolCall }, second);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls, 1);
  f.child.emit("close", null, "SIGKILL");
  const first: Event[] = [], other: Event[] = [];
  for await (const event of firstStream.events()) first.push(event);
  for await (const event of secondStream.events()) other.push(event);
  const unresolved = first.find((event) => event.type === "tool_result");
  const completed = other.find((event) => event.type === "tool_result");
  assert.equal(unresolved?.payload.resolution.outcome, "unknown");
  assert.equal(completed?.payload.resolution.outcome, "completed");
  assert.equal(completed?.payload.resolution.content, "second turn effect");
});

test("a callback with no valid owning turn ends the connection with a named error", async () => {
  for (const turn_id of [undefined, null, 0, ""]) {
    let calls = 0;
    const f = fixture(async () => { calls++; return "must not execute"; });
    const stream = f.connection.turn(info, () => {});
    f.send({ ...callback, turn_id });
    const events: Event[] = [];
    for await (const event of stream.events()) events.push(event);
    assert.equal(calls, 0);
    assert.ok(events.at(-1)?.type === "terminal");
    const terminal = events.at(-1)!;
    assert.ok(terminal.type === "terminal");
    assert.equal(terminal.payload.error?.code, "engine_unavailable");
    f.child.emit("close", 1, null);
  }
});
