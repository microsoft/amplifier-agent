/**
 * Test for Issue #7 — getEngineInfo() must return populated metadata.
 *
 * Previously the field was a Task-9 TODO that returned empty strings for
 * engineVersion and bundleDigest. This PR populates both from the engine
 * version probe that runs during spawnAgent (Issue #9).
 */

import { describe, it, expect } from "vitest";

import { spawnAgent, PROTOCOL_VERSION_REQUIRED_BY_WRAPPER } from "../src/index.js";
import type { SpawnAgentParams } from "../src/index.js";

const baseParams: SpawnAgentParams = {
  lifecycle: "one-shot",
  sessionId: "test-session",
  _binaryResolver: () => "/usr/local/bin/amplifier-agent",
};

describe("Issue #7 — getEngineInfo() populates engineVersion + bundleDigest", () => {
  it("returns the probed engine version and protocol version", async () => {
    const handle = await spawnAgent({
      ...baseParams,
      _engineVersionProbe: async () => ({
        version: "0.4.2",
        protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        bundleDigest: "sha256:abc123",
      }),
    });
    const info = handle.getEngineInfo();
    expect(info.engineVersion).toBe("0.4.2");
    expect(info.protocolVersion).toBe(PROTOCOL_VERSION_REQUIRED_BY_WRAPPER);
    expect(info.bundleDigest).toBe("sha256:abc123");
    expect(info.binaryPath).toBe("/usr/local/bin/amplifier-agent");
  });

  it("defaults bundleDigest to empty string when the engine omits it", async () => {
    const handle = await spawnAgent({
      ...baseParams,
      _engineVersionProbe: async () => ({
        version: "0.4.2",
        protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
      }),
    });
    const info = handle.getEngineInfo();
    expect(info.engineVersion).toBe("0.4.2");
    expect(info.bundleDigest).toBe("");
  });
});
