/**
 * SC-C: spawnAgent() must reject params.approval.onRequest loudly in v1.
 *
 * Per amendment §5.3, the Mode A wire has no mid-turn request channel.
 * Passing a non-null `onRequest` must throw `AaaError` with code
 * `approval_not_supported_in_v1`, classification `'protocol'`, BEFORE any
 * subprocess work is done.
 *
 * The earlier draft of the amendment had the wrapper accept the callback
 * and log a stderr warning; the SC-C adversarial review found that
 * warning-only acceptance ships silent auto-allow to a host author who
 * believed their callback was wired up. We reject loudly instead.
 */
import { describe, it, expect } from "vitest";
import { spawnAgent } from "../src/index.js";

describe("spawnAgent — SC-C: reject approval.onRequest loudly", () => {
  it("throws AaaError(approval_not_supported_in_v1) when params.approval.onRequest is provided", async () => {
    await expect(
      spawnAgent({
        lifecycle: "one-shot",
        sessionId: "sid",
        approval: {
          onRequest: async () => ({ decision: "allow" }),
        },
      } as Parameters<typeof spawnAgent>[0]),
    ).rejects.toMatchObject({
      name: "AaaError",
      code: "approval_not_supported_in_v1",
      classification: "protocol",
    });
  });
});
