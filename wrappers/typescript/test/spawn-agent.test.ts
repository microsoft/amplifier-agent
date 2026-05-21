/**
 * Tests for spawnAgent() public API + getEngineInfo().
 *
 * TDD bullets:
 * (a) spawnAgent() with FakeTransport returns SessionHandle whose
 *     getEngineInfo() returns protocolVersion='2026-05-aaa-v0' and
 *     binaryPath='/dev/null'
 * (b) spawnAgent() throws AaaError(lifecycle_unsupported) when lifecycle
 *     is not 'one-shot' (D10)
 */
import { describe, it, expect } from "vitest";
import { spawnAgent, AaaError } from "../src/index.js";
import type { ExitInfo } from "../src/transport.js";

/**
 * FakeTransport: implements the transport contract expected by spawnAgent().
 * Auto-responds to agent/initialize with a valid result.
 */
class FakeTransport {
  private readonly frameCallbacks: Array<(obj: unknown) => void> = [];

  async spawn(): Promise<void> {
    // no-op
  }

  onFrame(cb: (obj: unknown) => void): void {
    this.frameCallbacks.push(cb);
  }

  send(obj: unknown): void {
    const frame = obj as { id?: unknown; method?: string };
    if (frame.method === "agent/initialize") {
      const reqId = frame.id;
      // Schedule response on next tick so the JsonRpcClient future can be awaited first.
      setTimeout(() => {
        const response = {
          jsonrpc: "2.0",
          id: reqId,
          result: {
            capabilities: {},
            serverInfo: { name: "test-agent", version: "0.0.0" },
            sessionState: { sessionId: "fake-session-id", resumed: false },
          },
        };
        for (const cb of this.frameCallbacks) {
          cb(response);
        }
      }, 0);
    }
  }

  async terminate(): Promise<ExitInfo> {
    return { code: 0, signal: null };
  }
}

describe("spawnAgent", () => {
  it(
    "(a) returns SessionHandle with getEngineInfo() returning protocolVersion and binaryPath",
    async () => {
      const handle = await spawnAgent({
        lifecycle: "one-shot",
        sessionId: "test-session",
        _binaryResolver: () => "/dev/null",
        _versionProbe: (_binPath: string, _env: Record<string, string>) => ({
          version: "1.2.3",
          protocolVersion: "2026-05-aaa-v0",
          bundleDigest: "deadbeef",
        }),
        _transportFactory: () => new FakeTransport(),
      });

      const info = handle.getEngineInfo();
      expect(info.protocolVersion).toBe("2026-05-aaa-v0");
      expect(info.binaryPath).toBe("/dev/null");
    },
    5000,
  );

  it("(b) throws AaaError(lifecycle_unsupported) when lifecycle !== 'one-shot'", async () => {
    let caught: unknown;
    try {
      // Cast to bypass TS type — we're testing the runtime guard.
      await spawnAgent({ lifecycle: "burst" as "one-shot", sessionId: "x" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(AaaError);
    expect((caught as AaaError).code).toBe("lifecycle_unsupported");
  });
});
