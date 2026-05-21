/**
 * Tests for L14 client-side result/final synthesis (design §4.6 contract #1).
 *
 * Pure function tests:
 * (a) sawFinal=true  → returns null (no synthesis needed)
 * (b) reply=null     → returns null (nothing to synthesize from)
 * (c) sawFinal=false, reply='hello' → returns DisplayEvent with
 *     type='result/final', synthesized=true, payload.text='hello'
 *
 * Integration test:
 * (d) Branch B — engine omits result/final but provides reply in turn/submit
 *     response → last yielded event is synthesized result/final
 */
import { describe, it, expect } from "vitest";
import { synthesizeFinalIfMissing } from "../src/l14.js";
import { SessionHandle } from "../src/session.js";
import type { DisplayEvent } from "../src/session.js";

// ---------------------------------------------------------------------------
// Pure function tests
// ---------------------------------------------------------------------------

describe("synthesizeFinalIfMissing", () => {
  it("(a) returns null when sawFinal is true", () => {
    const result = synthesizeFinalIfMissing({
      sawFinal: true,
      reply: "hello",
      sessionId: "sess-1",
      turnId: "turn-1",
    });
    expect(result).toBeNull();
  });

  it("(b) returns null when reply is null", () => {
    const result = synthesizeFinalIfMissing({
      sawFinal: false,
      reply: null,
      sessionId: "sess-1",
      turnId: "turn-1",
    });
    expect(result).toBeNull();
  });

  it("(c) returns synthesized DisplayEvent when sawFinal=false and reply is provided", () => {
    const result = synthesizeFinalIfMissing({
      sawFinal: false,
      reply: "hello",
      sessionId: "sess-1",
      turnId: "turn-1",
    });
    expect(result).not.toBeNull();
    expect(result?.type).toBe("result/final");
    expect(result?.synthesized).toBe(true);
    expect(result?.payload["text"]).toBe("hello");
  });
});

// ---------------------------------------------------------------------------
// Integration test helpers (re-use StubRpc pattern from session.test.ts)
// ---------------------------------------------------------------------------

class StubRpc {
  private notifCallbacks: Array<
    (notif: { method: string; params?: unknown }) => void
  > = [];
  private pendingResolves: Array<{
    key: string;
    resolve: (v: unknown) => void;
  }> = [];
  private callCount = 0;

  call(method: string, _params?: unknown): Promise<unknown> {
    const key = `${method}:${this.callCount++}`;
    return new Promise<unknown>((resolve) => {
      this.pendingResolves.push({ key, resolve });
    });
  }

  onNotification(
    cb: (notif: { method: string; params?: unknown }) => void,
  ): void {
    this.notifCallbacks.push(cb);
  }

  notify(method: string, params: unknown): void {
    for (const cb of this.notifCallbacks) {
      cb({ method, params });
    }
  }

  resolveCall(method: string, result: unknown = null): void {
    const idx = this.pendingResolves.findIndex((p) =>
      p.key.startsWith(`${method}:`),
    );
    if (idx !== -1) {
      const entry = this.pendingResolves.splice(idx, 1)[0];
      entry!.resolve(result);
    }
  }
}

// ---------------------------------------------------------------------------
// Integration test — Branch B
// ---------------------------------------------------------------------------

describe("SessionHandle L14 integration", () => {
  it(
    "(d) Branch B: synthesizes result/final when engine omits it but provides reply",
    async () => {
      const rpc = new StubRpc();
      const handle = new SessionHandle(rpc, {
        sessionId: "sess-l14",
        terminate: async () => {},
      });

      const iter = handle.submit("hello");
      const events: DisplayEvent[] = [];

      const consuming = (async () => {
        for await (const evt of iter) {
          events.push(evt);
        }
      })();

      // Give the generator one tick to register notification callback
      await new Promise<void>((r) => setTimeout(r, 0));

      // Drive a delta event but NO result/final (Branch B scenario)
      rpc.notify("result/delta", {
        sessionId: "sess-l14",
        turnId: "turn-l14",
        text: "Hello",
      });

      // Resolve turn/submit with a reply — engine omits result/final
      rpc.resolveCall("turn/submit", {
        reply: "Hello",
        turnId: "turn-l14",
        sessionId: "sess-l14",
      });

      await consuming;

      expect(events.length).toBeGreaterThanOrEqual(2);
      const last = events[events.length - 1];
      expect(last?.type).toBe("result/final");
      expect(last?.synthesized).toBe(true);
      expect(last?.payload["text"]).toBe("Hello");
    },
    5000,
  );
});
