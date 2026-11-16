import assert from "node:assert/strict";
import { test } from "node:test";
import { agentOptions } from "../src/internal/callbacks.js";
import type { AgentOptions } from "../src/records.js";

test("smartTools marshals as smart_tools", () => {
  const cases: [Record<string, unknown>, Record<string, unknown>][] = [
    [{ smartTools: false }, { smart_tools: false }],
    [{ smartTools: true }, { smart_tools: true }],
    [{ smartTools: undefined }, {}],
  ];
  for (const [options, expected] of cases) assert.deepEqual(agentOptions(options as AgentOptions), expected);
});
