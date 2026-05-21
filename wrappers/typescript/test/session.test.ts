/**
 * Tests for SessionHandle.submit() returning AsyncIterable<DisplayEvent>.
 *
 * RED: fails because wrappers/typescript/src/session.ts does not exist yet.
 * GREEN: passes once SessionHandle is implemented.
 *
 * TDD bullets:
 * (a) yields display events then ends when result/final arrives —
 *     drive 2 result/delta notifs + result/final; collected event types
 *     should equal ['result/delta','result/delta','result/final']
 * (b) second submit() throws — one-shot per session (D10),
 *     matches /one-shot|already submitted/i
 */
import { describe, it, expect } from "vitest";
import { SessionHandle, AaaError } from "../src/session.js";
import type { DisplayEvent } from "../src/session.js";

/** Minimal stub RPC for testing: captures sent calls, exposes methods
 *  to simulate incoming notifications and resolve pending calls. */
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

  /** Simulate an incoming notification from the server. */
  notify(method: string, params: unknown): void {
    for (const cb of this.notifCallbacks) {
      cb({ method, params });
    }
  }

  /** Resolve the first pending call for the given method. */
  resolveCall(method: string, result: unknown = null): void {
    const idx = this.pendingResolves.findIndex((p) => p.key.startsWith(`${method}:`));
    if (idx !== -1) {
      const entry = this.pendingResolves.splice(idx, 1)[0];
      entry!.resolve(result);
    }
  }
}

describe("SessionHandle", () => {
  it(
    "(a) yields display events and ends on result/final",
    async () => {
      const rpc = new StubRpc();
      const handle = new SessionHandle(rpc, {
        sessionId: "sess-1",
        terminate: async () => {},
      });

      const iter = handle.submit("hello");
      const events: DisplayEvent[] = [];

      // Start consuming in background — don't await yet
      const consuming = (async () => {
        for await (const evt of iter) {
          events.push(evt);
        }
      })();

      // Give the generator one tick to register notification callback
      await new Promise<void>((r) => setTimeout(r, 0));

      // Drive 2 result/delta notifications then result/final
      rpc.notify("result/delta", {
        sessionId: "sess-1",
        turnId: "turn-1",
        text: "Hello",
      });
      rpc.notify("result/delta", {
        sessionId: "sess-1",
        turnId: "turn-1",
        text: " World",
      });
      rpc.notify("result/final", {
        sessionId: "sess-1",
        turnId: "turn-1",
        text: "Hello World",
      });

      // Resolve turn/submit RPC response (comes after result/final in normal flow)
      rpc.resolveCall("turn/submit", {
        reply: "Hello World",
        turnId: "turn-1",
        sessionId: "sess-1",
      });

      await consuming;

      const types = events.map((e) => e.type);
      expect(types).toEqual(["result/delta", "result/delta", "result/final"]);
    },
    5000,
  );

  it("(b) second submit() throws one-shot error", () => {
    const rpc = new StubRpc();
    const handle = new SessionHandle(rpc, {
      sessionId: "sess-2",
      terminate: async () => {},
    });

    // First submit is fine
    handle.submit("first");

    // Second submit should throw AaaError matching /one-shot|already submitted/i
    let caught: unknown;
    try {
      handle.submit("second");
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(AaaError);
    expect((caught as AaaError).message).toMatch(/one-shot|already submitted/i);
  });
});
