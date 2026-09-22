import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { AgentError, contractVersion, contractVersions, version } from "../src/index.js";
import { decode, encode, receiveEvent } from "../src/internal/codec.js";
import { agentOptions } from "../src/internal/callbacks.js";
import type { Event } from "@microsoft/amplifier-agent";

test("imports declare immutable contract versions", async () => {
  assert.equal(contractVersion, "agent-interface/1");
  assert.deepEqual(contractVersions, ["agent-interface/1", "turn-events/1", "language-binding/1", "host-config/1"]);
  assert.ok(Object.isFrozen(contractVersions));
  assert.match(version, /^\d+\.\d+\.\d+/);
  const metadata = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8")) as { version: string };
  assert.equal(version, metadata.version);
});

test("exact event integers, decimals, and owned fields survive record conversion", () => {
  const event = receiveEvent(decode('{"contract_version":"turn-events/1","session_id":"session-1","turn_id":"turn-1","sequence":1,"type":"usage","payload":{"snapshot":{"entries":[{"provider":"anthropic","model":"claude-sonnet-5","tokens_in":9007199254740993,"tokens_out":2,"cost":{"USD":"0.0000000000000000001234"},"org.example.counter":9007199254740995}]}},"org.example.extension":{"small":2,"large":9007199254740997}}') as Event);
  assert.equal(event.sequence, 1n);
  assert.ok(event.type === "usage");
  assert.equal(event.payload.snapshot.entries[0]?.tokens_in, 9007199254740993n);
  assert.equal(event.payload.snapshot.entries[0]?.tokens_out, 2n);
  assert.equal(event.payload.snapshot.entries[0]?.cost?.USD, "0.0000000000000000001234");
  const raw = event as unknown as Record<string, unknown>;
  assert.deepEqual(raw["org.example.extension"], { small: 2, large: 9007199254740997n });
  assert.equal(encode({ value: 9007199254740997n }), '{"value":9007199254740997}');
});

test("error records retain remedy, retryability, correlation, and exact details", () => {
  const event = receiveEvent(decode('{"contract_version":"turn-events/1","session_id":"session-1","turn_id":"turn-1","sequence":2,"type":"terminal","payload":{"state":"failure","error":{"code":"provider_failed","category":"provider","message":"Request failed","remedy":"Check the configured credential.","retryable":false,"correlation_id":"correlation-1","details":{"org.example.value":9007199254740993},"org.example.extra":"preserved"}}}') as Event);
  assert.ok(event.type === "terminal");
  assert.ok(event.payload.error instanceof AgentError);
  assert.equal(event.payload.error.correlation_id, "correlation-1");
  assert.equal(event.payload.error.retryable, false);
  assert.deepEqual(event.payload.error.details, { "org.example.value": 9007199254740993n });
  assert.equal((event.payload.error as unknown as Record<string, unknown>)["org.example.extra"], "preserved");
});

test("strict JSON conversion rejects lossy, cyclic, and non-finite input", () => {
  const circular: Record<string, unknown> = {}; circular.self = circular;
  for (const value of [{ number: Number.MAX_SAFE_INTEGER + 1 }, { number: NaN }, { number: Infinity }, { missing: undefined }, circular]) {
    assert.throws(() => encode(value), (error) => error instanceof AgentError && error.code === "invalid_input" && error.remedy.length > 0);
  }
});

test("tool recovery options marshal by their contract name without ambient defaults", () => {
  assert.deepEqual(agentOptions({ toolErrorPolicy: "continue" }), { tool_error_policy: "continue" });
  assert.deepEqual(agentOptions({ toolErrorPolicy: "stop" }), { tool_error_policy: "stop" });
  assert.deepEqual(agentOptions({ toolResultMaxBytes: 64 }), { tool_result_max_bytes: 64 });
  assert.deepEqual(agentOptions({ toolResultMaxBytes: null }), { tool_result_max_bytes: null });
  assert.deepEqual(agentOptions({}), {});
});

test("blocked recovery retains its executor error and uncertain call correlation", () => {
  const event = receiveEvent(decode('{"contract_version":"turn-events/1","session_id":"session-1","turn_id":"turn-1","sequence":2,"type":"tool_result","payload":{"resolution":{"call_id":"blocked-1","outcome":"cancelled","error":{"code":"tool_recovery_blocked","category":"executor","message":"An earlier effect has an uncertain outcome.","remedy":"Inspect the earlier effect before requesting more work in a new turn.","retryable":false,"correlation_id":"blocked-1","details":{"uncertain_call_id":"unknown-1"}}}}}') as Event);
  assert.ok(event.type === "tool_result");
  assert.ok(event.payload.resolution.error instanceof AgentError);
  assert.equal(event.payload.resolution.error.code, "tool_recovery_blocked");
  assert.equal(event.payload.resolution.error.category, "executor");
  assert.equal(event.payload.resolution.error.correlation_id, "blocked-1");
  assert.deepEqual(event.payload.resolution.error.details, { uncertain_call_id: "unknown-1" });
  assert.equal(event.payload.resolution.error.retryable, false);
});
