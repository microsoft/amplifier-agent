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

  it("initialize_with_mcp_config_path passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/initialize-with-mcp-config-path.yaml`,
    );
    expect(report.passed).toBe(true);
  });

  it("approval_shim_three_error_codes passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/approval-shim-three-error-codes.yaml`,
    );
    expect(report.passed).toBe(true);
  });

  it("resume_with_session_store passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/resume-with-session-store.yaml`,
    );
    expect(report.passed).toBe(true);
  });

  it("initialize_baseline passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/initialize-baseline.yaml`,
    );
    expect(report.passed).toBe(true);
  });

  it("initialize_with_protocol_skew_override passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/initialize-with-protocol-skew-override.yaml`,
    );
    expect(report.passed).toBe(true);
  });
});
