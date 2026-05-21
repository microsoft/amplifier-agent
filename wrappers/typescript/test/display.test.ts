/**
 * Tests for display adapter: onEvent push callback + subagent event filtering.
 *
 * RED: fails because wrappers/typescript/src/display.ts does not exist yet.
 * GREEN: passes once applyDisplayFilter is implemented.
 *
 * Three filter cases:
 * (a) keeps everything when subagentEvents='all' (including events with parentTurnId)
 * (b) drops events with parentTurnId when subagentEvents='none'
 * (c) defaults to 'all' when subagentEvents unset
 *
 * Integration cases:
 * (d) push callback (onEvent) receives kept events when wired into SessionHandle
 * (e) filter suppresses parentTurnId events from both iterator and push callback when subagentEvents='none'
 */
import { describe, it, expect, vi } from "vitest";
import { applyDisplayFilter } from "../src/display.js";
import type { DisplayAdapter } from "../src/display.js";
import { SessionHandle } from "../src/session.js";
import type { DisplayEvent } from "../src/session.js";

// ---------------------------------------------------------------------------
// Unit tests: applyDisplayFilter
// ---------------------------------------------------------------------------
describe("applyDisplayFilter", () => {
  const eventWithParentTurnId: DisplayEvent = {
    type: "result/delta",
    sessionId: "s1",
    turnId: "t1",
    parentTurnId: "parent-turn-1",
    payload: { parentTurnId: "parent-turn-1" },
  };

  const eventWithoutParentTurnId: DisplayEvent = {
    type: "result/delta",
    sessionId: "s1",
    turnId: "t1",
    payload: {},
  };

  it("(a) subagentEvents='all': keeps events with and without parentTurnId", () => {
    const adapter: DisplayAdapter = { subagentEvents: "all" };
    const keep = applyDisplayFilter(adapter);
    expect(keep(eventWithParentTurnId)).toBe(true);
    expect(keep(eventWithoutParentTurnId)).toBe(true);
  });

  it("(b) subagentEvents='none': drops events with parentTurnId, keeps those without", () => {
    const adapter: DisplayAdapter = { subagentEvents: "none" };
    const keep = applyDisplayFilter(adapter);
    expect(keep(eventWithParentTurnId)).toBe(false);
    expect(keep(eventWithoutParentTurnId)).toBe(true);
  });

  it("(c) subagentEvents unset: defaults to 'all' (keeps everything)", () => {
    const adapter: DisplayAdapter = {};
    const keep = applyDisplayFilter(adapter);
    expect(keep(eventWithParentTurnId)).toBe(true);
    expect(keep(eventWithoutParentTurnId)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Integration tests: onEvent push callback + filtering wired into SessionHandle
// ---------------------------------------------------------------------------

/** Minimal stub RPC for integration tests. */
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

describe("SessionHandle with DisplayAdapter", () => {
  it(
    "(d) onEvent push callback receives kept events",
    async () => {
      const rpc = new StubRpc();
      const pushed: DisplayEvent[] = [];
      const adapter: DisplayAdapter = {
        onEvent: (ev) => pushed.push(ev),
      };
      const handle = new SessionHandle(
        rpc,
        { sessionId: "sess-d", terminate: async () => {} },
        undefined,
        adapter,
      );

      const iter = handle.submit("hello");
      const pulled: DisplayEvent[] = [];
      const consuming = (async () => {
        for await (const evt of iter) {
          pulled.push(evt);
        }
      })();

      await new Promise<void>((r) => setTimeout(r, 0));

      rpc.notify("result/delta", { sessionId: "sess-d", turnId: "t1", text: "hi" });
      rpc.notify("result/final", { sessionId: "sess-d", turnId: "t1", text: "hi" });
      rpc.resolveCall("turn/submit", { reply: "hi", turnId: "t1", sessionId: "sess-d" });

      await consuming;

      // push and pull should see the same events
      expect(pulled.map((e) => e.type)).toEqual(["result/delta", "result/final"]);
      expect(pushed.map((e) => e.type)).toEqual(["result/delta", "result/final"]);
    },
    5000,
  );

  it(
    "(e) subagentEvents='none' suppresses parentTurnId events from both iterator and push callback",
    async () => {
      const rpc = new StubRpc();
      const pushed: DisplayEvent[] = [];
      const adapter: DisplayAdapter = {
        subagentEvents: "none",
        onEvent: (ev) => pushed.push(ev),
      };
      const handle = new SessionHandle(
        rpc,
        { sessionId: "sess-e", terminate: async () => {} },
        undefined,
        adapter,
      );

      const iter = handle.submit("hello");
      const pulled: DisplayEvent[] = [];
      const consuming = (async () => {
        for await (const evt of iter) {
          pulled.push(evt);
        }
      })();

      await new Promise<void>((r) => setTimeout(r, 0));

      // sub-agent progress event (should be filtered out)
      rpc.notify("result/delta", {
        sessionId: "sess-e",
        turnId: "t2",
        parentTurnId: "parent-t1",
        text: "sub-agent chunk",
      });
      // normal event (should pass through)
      rpc.notify("result/delta", { sessionId: "sess-e", turnId: "t1", text: "normal" });
      rpc.notify("result/final", { sessionId: "sess-e", turnId: "t1", text: "done" });
      rpc.resolveCall("turn/submit", { reply: "done", turnId: "t1", sessionId: "sess-e" });

      await consuming;

      // parentTurnId event should be suppressed from both paths
      expect(pulled.map((e) => e.type)).toEqual(["result/delta", "result/final"]);
      expect(pushed.map((e) => e.type)).toEqual(["result/delta", "result/final"]);
    },
    5000,
  );
});
