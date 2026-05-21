/**
 * Tests for JsonRpcClient: per-request-id correlation + notification fanout.
 *
 * RED: fails because wrappers/typescript/src/jsonrpc.ts does not exist yet.
 * GREEN: passes once JsonRpcClient is implemented.
 *
 * TDD bullets:
 * (a) call() resolves when matching result arrives
 * (b) two concurrent calls do not interfere (different ids, can resolve in reverse order)
 * (c) notifications fanned out to subscribers via onNotification
 * (d) server-initiated request invokes the registered onRequest handler and sends back
 *     {jsonrpc:'2.0', id, result}
 */
import { describe, it, expect } from "vitest";
import { JsonRpcClient } from "../src/jsonrpc.js";

/** Minimal stub transport for testing: captures sent frames, exposes a
 *  method to simulate incoming frames from the "server". */
class StubTransport {
  sent: unknown[] = [];
  private frameCallbacks: Array<(obj: unknown) => void> = [];

  send(obj: unknown): void {
    this.sent.push(obj);
  }

  onFrame(cb: (obj: unknown) => void): void {
    this.frameCallbacks.push(cb);
  }

  /** Simulate an incoming frame from the server. */
  receive(obj: unknown): void {
    for (const cb of this.frameCallbacks) {
      cb(obj);
    }
  }
}

describe("JsonRpcClient", () => {
  it(
    "(a) call() resolves when matching result arrives",
    async () => {
      const transport = new StubTransport();
      const client = new JsonRpcClient(transport);

      const resultPromise = client.call("echo", { msg: "hello" });

      // Verify a request was sent with the correct structure
      expect(transport.sent).toHaveLength(1);
      const sent = transport.sent[0] as Record<string, unknown>;
      expect(sent["jsonrpc"]).toBe("2.0");
      expect(sent["method"]).toBe("echo");
      expect(sent["params"]).toEqual({ msg: "hello" });
      const id = sent["id"] as number;
      expect(typeof id).toBe("number");

      // Simulate server sending back a result
      transport.receive({ jsonrpc: "2.0", id, result: { echo: "hello" } });

      const result = await resultPromise;
      expect(result).toEqual({ echo: "hello" });
    },
  );

  it(
    "(b) two concurrent calls do not interfere (NC-L16 designed out)",
    async () => {
      const transport = new StubTransport();
      const client = new JsonRpcClient(transport);

      const p1 = client.call("method1", { x: 1 });
      const p2 = client.call("method2", { y: 2 });

      // Two frames should have been sent with different ids
      expect(transport.sent).toHaveLength(2);
      const frame1 = transport.sent[0] as Record<string, unknown>;
      const frame2 = transport.sent[1] as Record<string, unknown>;
      const id1 = frame1["id"] as number;
      const id2 = frame2["id"] as number;
      expect(id1).not.toBe(id2);

      // Resolve in reverse order: p2 first, then p1
      transport.receive({ jsonrpc: "2.0", id: id2, result: "result2" });
      transport.receive({ jsonrpc: "2.0", id: id1, result: "result1" });

      const [r1, r2] = await Promise.all([p1, p2]);
      expect(r1).toBe("result1");
      expect(r2).toBe("result2");
    },
  );

  it(
    "(c) notifications fanned out to subscribers via onNotification",
    async () => {
      const transport = new StubTransport();
      const client = new JsonRpcClient(transport);

      const received1: unknown[] = [];
      const received2: unknown[] = [];
      client.onNotification((notif) => received1.push(notif));
      client.onNotification((notif) => received2.push(notif));

      // Simulate server sending a notification (no id, has method)
      transport.receive({
        jsonrpc: "2.0",
        method: "status_update",
        params: { status: "running" },
      });

      expect(received1).toHaveLength(1);
      expect(received2).toHaveLength(1);
      expect(received1[0]).toEqual({
        method: "status_update",
        params: { status: "running" },
      });
      expect(received2[0]).toEqual({
        method: "status_update",
        params: { status: "running" },
      });
    },
  );

  it(
    "(d) server-initiated request invokes onRequest handler and sends back result",
    async () => {
      const transport = new StubTransport();
      const client = new JsonRpcClient(transport);

      // Register a handler for "approval/request"
      client.onRequest("approval/request", async (params) => {
        return { approved: true, params };
      });

      // Simulate a server-initiated request
      transport.receive({
        jsonrpc: "2.0",
        id: 99,
        method: "approval/request",
        params: { action: "proceed" },
      });

      // Give the async handler time to run
      await new Promise<void>((resolve) => setTimeout(resolve, 10));

      // Verify the response was sent back
      expect(transport.sent).toHaveLength(1);
      const response = transport.sent[0] as Record<string, unknown>;
      expect(response["jsonrpc"]).toBe("2.0");
      expect(response["id"]).toBe(99);
      expect(response["result"]).toEqual({
        approved: true,
        params: { action: "proceed" },
      });
    },
  );

  it(
    "(d-error) unknown server method returns -32601 error",
    async () => {
      const transport = new StubTransport();
      const client = new JsonRpcClient(transport);

      // Simulate a server-initiated request with no registered handler
      transport.receive({
        jsonrpc: "2.0",
        id: 42,
        method: "unknown/method",
        params: {},
      });

      // Give the async handler time to run
      await new Promise<void>((resolve) => setTimeout(resolve, 10));

      expect(transport.sent).toHaveLength(1);
      const response = transport.sent[0] as Record<string, unknown>;
      expect(response["jsonrpc"]).toBe("2.0");
      expect(response["id"]).toBe(42);
      expect((response["error"] as Record<string, unknown>)["code"]).toBe(-32601);
    },
  );
});
