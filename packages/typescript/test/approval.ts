import assert from "node:assert/strict";
import { AgentError } from "@microsoft/amplifier-agent";
import type { ApprovalResolution, Event } from "@microsoft/amplifier-agent";

export function assertApprovalOutcome(events: Event[], decision: ApprovalResolution["decision"]): void {
  const terminal = events.at(-1);
  assert.ok(terminal?.type === "terminal");
  assert.equal(events.filter((event) => event.type === "terminal").length, 1);
  assert.equal(terminal.payload.state, decision === "allow" ? "success" : decision === "deny" ? "rejected" : decision === "cancel" ? "cancelled" : "failure");
  const calls = events.flatMap((event) => event.type === "tool_call" ? [event.payload.call] : []);
  const results = events.flatMap((event) => event.type === "tool_result" ? [event.payload.resolution] : []);
  const requests = events.flatMap((event) => event.type === "approval_request" ? [event.payload.request] : []);
  const decisions = events.flatMap((event) => event.type === "approval_decision" ? [event.payload.resolution] : []);
  assert.equal(calls.length, 1);
  assert.equal(results.length, 1);
  assert.equal(requests.length, 1);
  assert.equal(decisions.length, 1);
  assert.equal(results[0]!.call_id, calls[0]!.call_id);
  assert.equal(requests[0]!.call_id, calls[0]!.call_id);
  assert.equal(decisions[0]!.request_id, requests[0]!.request_id);
  assert.equal(decisions[0]!.decision, decision);
  if (decision !== "allow") {
    const error = terminal.payload.error;
    assert.ok(error instanceof AgentError);
    assert.equal(error.code, decision === "deny" ? "approval_denied" : decision === "cancel" ? "approval_cancelled" : `approval_${decision}`);
    assert.equal(error.retryable, false);
    assert.equal(error.correlation_id, requests[0]!.request_id);
    assert.ok(error.remedy.length > 0);
  }
}
