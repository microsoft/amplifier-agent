/**
 * Conformance runner tests — TypeScript.
 *
 * Tests that runFixture() passes for the two required fixtures.
 * RED: fails because runner_ts.ts does not exist yet.
 * GREEN: passes once runner_ts.ts is implemented.
 */
import { describe, it, expect } from "vitest";
import { runFixture } from "../runner_ts.js";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const FIXTURES_DIR = resolve(
  __dirname,
  "../../../src/amplifier_agent_lib/protocol/conformance/fixtures",
);

describe("conformance runner (typescript)", () => {
  it("capability_negotiation passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/capability_negotiation.yaml`,
    );
    expect(report.passed).toBe(true);
  });

  it("l14_synthesis passes", async () => {
    const report = await runFixture(`${FIXTURES_DIR}/l14_synthesis.yaml`);
    expect(report.passed).toBe(true);
  });
});
