/**
 * Tests for the approval bridge — in-band, mid-turn JSON-RPC round-trip (§5.2).
 *
 * RED: fails because wrappers/typescript/src/approval.ts does not exist yet.
 * GREEN: passes once makeApprovalHandler is implemented and wired into session.ts.
 *
 * Three cases:
 * (a) forwards request to adapter and returns its response {decision:'allow', requestId}
 * (b) emits decision='timeout' if onRequest exceeds timeoutMs (never-resolving promise, timeoutMs=50)
 * (c) falls back to {decision:'deny'} when no adapter configured
 */
import { describe, it, expect } from "vitest";
import { makeApprovalHandler } from "../src/approval.js";
import type { ApprovalAdapter, ApprovalResponse } from "../src/approval.js";

describe("makeApprovalHandler", () => {
  it(
    "(a) forwards request to adapter and returns its response",
    async () => {
      const adapter: ApprovalAdapter = {
        onRequest: async (req) => ({
          decision: "allow" as const,
          requestId: (req as Record<string, unknown>)["id"] as string,
        }),
        timeoutMs: 1000,
      };
      const handler = makeApprovalHandler(adapter);
      const result = (await handler({ id: "req-1", tool: "bash", args: {} })) as ApprovalResponse & {
        requestId?: string;
      };
      expect(result.decision).toBe("allow");
      expect(result.requestId).toBe("req-1");
    },
    5000,
  );

  it(
    "(b) emits decision=timeout if onRequest exceeds timeoutMs",
    async () => {
      const adapter: ApprovalAdapter = {
        onRequest: () => new Promise<ApprovalResponse>(() => {}), // never resolves
        timeoutMs: 50,
      };
      const handler = makeApprovalHandler(adapter);
      const result = (await handler({ id: "req-2", tool: "bash", args: {} })) as ApprovalResponse;
      expect(result.decision).toBe("timeout");
    },
    5000,
  );

  it(
    "(c) falls back to deny when no adapter configured",
    async () => {
      const handler = makeApprovalHandler(undefined);
      const result = (await handler({ id: "req-3", tool: "bash", args: {} })) as ApprovalResponse;
      expect(result.decision).toBe("deny");
      expect(result.reason).toBe("no_adapter_configured");
    },
    5000,
  );
});
