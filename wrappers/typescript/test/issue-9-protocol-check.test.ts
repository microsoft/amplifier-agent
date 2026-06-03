/**
 * Test for Issue #9 — checkProtocolVersion() must be wired into the init
 * path (spawnAgent), failing fast wrapper-side before any subprocess spawn
 * when wrapper's pinned protocol version doesn't match the engine's.
 *
 * The utility exists (src/version.ts:checkProtocolVersion) but is never
 * called. This test pins the contract: spawnAgent invokes the protocol
 * probe and throws AaaError(protocol_version_mismatch) on skew.
 */

import { describe, it, expect } from "vitest";

import { spawnAgent, AaaError, PROTOCOL_VERSION_REQUIRED_BY_WRAPPER } from "../src/index.js";
import type { SpawnAgentParams } from "../src/index.js";

const baseParams: SpawnAgentParams = {
  lifecycle: "one-shot",
  sessionId: "test-session",
  _binaryResolver: () => "/usr/bin/false", // never actually spawned in these tests
};

describe("Issue #9 — spawnAgent wires checkProtocolVersion()", () => {
  it("throws AaaError(protocol_version_mismatch) when engine speaks a different protocol", async () => {
    await expect(
      spawnAgent({
        ...baseParams,
        // Inject a probe payload reporting a mismatched protocol.
        _engineVersionProbe: async () => ({
          version: "0.4.0",
          protocolVersion: "0.99.99",
        }),
      }),
    ).rejects.toThrow(AaaError);

    try {
      await spawnAgent({
        ...baseParams,
        _engineVersionProbe: async () => ({
          version: "0.4.0",
          protocolVersion: "0.99.99",
        }),
      });
    } catch (e) {
      expect(e).toBeInstanceOf(AaaError);
      expect((e as AaaError).code).toBe("protocol_version_mismatch");
      expect((e as AaaError).message).toContain("0.99.99");
      expect((e as AaaError).message).toContain(PROTOCOL_VERSION_REQUIRED_BY_WRAPPER);
    }
  });

  it("succeeds when engine and wrapper protocols match", async () => {
    const handle = await spawnAgent({
      ...baseParams,
      _engineVersionProbe: async () => ({
        version: "0.4.0",
        protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
      }),
    });
    expect(handle).toBeDefined();
  });

  it("bypasses the check when allowProtocolSkew=true is set", async () => {
    const handle = await spawnAgent({
      ...baseParams,
      allowProtocolSkew: true,
      _engineVersionProbe: async () => ({
        version: "0.4.0",
        protocolVersion: "999.999.999",
      }),
    });
    expect(handle).toBeDefined();
  });
});
