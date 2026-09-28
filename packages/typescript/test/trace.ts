import assert from "node:assert/strict";
import type { Event, Turn, TurnResult } from "@microsoft/amplifier-agent";
import { AgentError } from "@microsoft/amplifier-agent";

export function named(code: string): (error: unknown) => boolean {
  return (error) => {
    assert.ok(error instanceof AgentError, "Errors must use the public native error class.");
    assert.equal(error.code, code);
    assert.ok(error.message.length > 0, "Errors must name the failure.");
    assert.ok(error.remedy.length > 0, "Errors must provide an actionable remedy.");
    assert.equal(typeof error.retryable, "boolean");
    return true;
  };
}

export async function collect(turn: Turn): Promise<Event[]> {
  const events: Event[] = [];
  for await (const event of turn.events()) events.push(event);
  return events;
}

export function trace(events: Event[]): TurnResult {
  assert.equal(events[0]?.type, "turn_started");
  assert.equal(events.filter((event) => event.type === "terminal").length, 1);
  const terminal = events.at(-1);
  assert.ok(terminal?.type === "terminal", "A trace must end with terminal.");
  for (const [index, event] of events.entries()) assert.equal(event.sequence, BigInt(index + 1));
  const ids = (type: string, of: (event: Event) => string) =>
    events
      .filter((e) => e.type === type)
      .map(of)
      .sort();
  const call = (event: Event) => (event.type === "tool_call" ? event.payload.call.call_id : "");
  const result = (event: Event) => (event.type === "tool_result" ? event.payload.resolution.call_id : "");
  assert.deepEqual(ids("tool_result", result), ids("tool_call", call), "Every call resolves exactly once.");
  const request = (event: Event) => (event.type === "approval_request" ? event.payload.request.request_id : "");
  const decision = (event: Event) => (event.type === "approval_decision" ? event.payload.resolution.request_id : "");
  assert.deepEqual(ids("approval_decision", decision), ids("approval_request", request));
  assert.deepEqual(
    terminal.payload.content ?? [],
    events.flatMap((event) => (event.type === "output_delta" ? event.payload.content : [])),
    "Terminal content reconstructs the streamed deltas.",
  );
  for (const event of events) {
    assert.deepEqual(
      [event.session_id, event.turn_id],
      [terminal.session_id, terminal.turn_id],
      `${event.type} belongs to another turn.`,
    );
  }
  if (terminal.payload.usage !== undefined) {
    const usage = events.filter((event) => event.type === "usage").at(-1);
    assert.ok(usage?.type === "usage", "Terminal usage requires a preceding usage event.");
    assert.deepEqual(terminal.payload.usage, usage.payload.snapshot, "Terminal usage equals the last usage snapshot.");
  }
  if (terminal.payload.state === "success") assert.equal(terminal.payload.error, undefined, "Success omits error.");
  if (terminal.payload.state !== "success") named(terminal.payload.error?.code ?? "")(terminal.payload.error);
  return terminal.payload;
}
