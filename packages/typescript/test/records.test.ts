import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import type { Event } from "@microsoft/amplifier-agent";
import { AgentError, BUILTIN_TOOLS, contractVersion, contractVersions, version } from "../src/index.js";
import { agentOptions } from "../src/internal/callbacks.js";
import { decode, encode, receiveEvent } from "../src/internal/codec.js";

test("imports declare immutable contract versions", async () => {
  assert.equal(contractVersion, "agent-interface/1");
  assert.deepEqual(contractVersions, ["agent-interface/1", "turn-events/1", "language-binding/1", "host-config/1"]);
  assert.ok(Object.isFrozen(contractVersions));
  assert.match(version, /^\d+\.\d+\.\d+/);
  const metadata = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8")) as {
    version: string;
  };
  assert.equal(version, metadata.version);
});

test("exact event integers, decimals, and owned fields survive record conversion", () => {
  const event = receiveEvent(
    decode(
      '{"contract_version":"turn-events/1","session_id":"session-1","turn_id":"turn-1","sequence":1,"type":"usage","payload":{"snapshot":{"entries":[{"provider":"anthropic","model":"claude-sonnet-5","tokens_in":9007199254740993,"tokens_out":2,"cost":{"USD":"0.0000000000000000001234"},"org.example.counter":9007199254740995}]}},"org.example.extension":{"small":2,"large":9007199254740997}}',
    ) as Event,
  );
  assert.equal(event.sequence, 1n);
  assert.ok(event.type === "usage");
  assert.equal(event.payload.snapshot.entries[0]?.tokens_in, 9007199254740993n);
  assert.equal(event.payload.snapshot.entries[0]?.tokens_out, 2n);
  assert.equal(event.payload.snapshot.entries[0]?.cost?.USD, "0.0000000000000000001234");
  const raw = event as unknown as Record<string, unknown>;
  assert.deepEqual(raw["org.example.extension"], { small: 2, large: 9007199254740997n });
  assert.equal(encode({ value: 9007199254740997n }), '{"value":9007199254740997}');
});

const failure = {
  code: "provider_failed",
  category: "provider",
  message: "Request failed",
  remedy: "Check the configured credential.",
  retryable: false,
  correlation_id: "correlation-1",
  details: { "org.example.value": 9007199254740993n },
  "org.example.extra": "preserved",
};
const wireError =
  '{"code":"provider_failed","category":"provider","message":"Request failed","remedy":"Check the configured credential.","retryable":false,"correlation_id":"correlation-1","details":{"org.example.value":9007199254740993},"org.example.extra":"preserved"}';
const envelope = (type: string, payload: string) =>
  `{"contract_version":"turn-events/1","session_id":"session-1","turn_id":"turn-1","sequence":2,"type":"${type}","payload":${payload}}`;

for (const [path, event] of [
  ["terminal", envelope("terminal", `{"state":"failure","error":${wireError}}`)],
  [
    "tool_result",
    envelope("tool_result", `{"resolution":{"call_id":"call-1","outcome":"unknown","error":${wireError}}}`),
  ],
] as const) {
  test(`error records in ${path} events retain remedy, retryability, correlation, and exact details`, () => {
    const received = receiveEvent(decode(event) as Event);
    const error =
      received.type === "terminal"
        ? received.payload.error
        : received.type === "tool_result"
          ? received.payload.resolution.error
          : undefined;
    assert.ok(error instanceof AgentError);
    const { name: _name, ...fields } = error;
    assert.deepEqual({ ...fields, message: error.message }, failure);
  });
}

test("strict JSON conversion rejects lossy, cyclic, and non-finite input", () => {
  const circular: Record<string, unknown> = {};
  circular.self = circular;
  for (const value of [
    { number: Number.MAX_SAFE_INTEGER + 1 },
    { number: NaN },
    { number: Infinity },
    { missing: undefined },
    circular,
  ]) {
    assert.throws(
      () => encode(value),
      (error) => error instanceof AgentError && error.code === "invalid_input" && error.remedy.length > 0,
    );
  }
});

test("agent options marshal by their contract names without ambient defaults", () => {
  const handler = async () => "noted";
  const schema = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };
  for (const [options, expected] of [
    [{}, {}],
    [{ toolErrorPolicy: "continue" }, { tool_error_policy: "continue" }],
    [{ toolErrorPolicy: "stop" }, { tool_error_policy: "stop" }],
    [{ toolResultMaxBytes: 64 }, { tool_result_max_bytes: 64 }],
    [{ toolResultMaxBytes: null }, { tool_result_max_bytes: null }],
    [{ tools: [] }, { tools: [] }],
    [
      { tools: ["read_file", { name: "note", description: "Note.", inputSchema: schema, handler }, "grep"] },
      { tools: ["read_file", { name: "note", description: "Note.", input_schema: schema }, "grep"] },
    ],
  ] as const)
    assert.deepEqual(agentOptions(options as Parameters<typeof agentOptions>[0]), expected);
});

test("the built-in tool names are one frozen list in contract order", () => {
  assert.deepEqual(BUILTIN_TOOLS, [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    "web_fetch",
    "web_search",
    "delegate",
  ]);
  assert.ok(Object.isFrozen(BUILTIN_TOOLS));
});
