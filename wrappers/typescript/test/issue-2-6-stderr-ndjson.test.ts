/**
 * Test for Issue #2 + #6 — wire Transport NDJSON pipeline on stderr.
 *
 * The engine emits one JSON object per line on stderr for each
 * wire-protocol notification (the 9 wire event types). The wrapper
 * currently buffers stderr as raw text and never parses it; the
 * existing `Transport` class is dead code.
 *
 * This test pins:
 *   1. parseNdjsonStream() — standalone helper that parses NDJSON.
 *   2. SessionHandle wires parseNdjsonStream onto the child's stderr
 *      stream so JSON lines flow through to display.onEvent (Issue #4)
 *      while non-JSON lines are still preserved in the stderrTail
 *      surface (backward-compatible for parseRunOutput).
 */

import { describe, it, expect } from "vitest";
import { EventEmitter } from "node:events";
import { Readable } from "node:stream";

import {
  spawnAgent,
  parseNdjsonStream,
  PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
} from "../src/index.js";
import type { SpawnAgentParams, ChildProcessFactory } from "../src/index.js";

describe("Issue #2 + #6 — stderr NDJSON pipeline", () => {
  describe("parseNdjsonStream standalone", () => {
    it("dispatches each JSON line to onJson", async () => {
      const events: unknown[] = [];
      const stream = Readable.from([
        Buffer.from(
          '{"method":"progress","params":{"message":"step 1"}}\n' +
            '{"method":"tool/started","params":{"name":"bash"}}\n',
        ),
      ]);
      await parseNdjsonStream(stream, { onJson: (obj) => events.push(obj) });
      expect(events).toHaveLength(2);
      expect((events[0] as { method: string }).method).toBe("progress");
      expect((events[1] as { method: string }).method).toBe("tool/started");
    });

    it("dispatches non-JSON lines to onNonJson when provided", async () => {
      const json: unknown[] = [];
      const nonJson: string[] = [];
      const stream = Readable.from([
        Buffer.from(
          '{"method":"progress","params":{}}\n' +
            "not json\n" +
            '{"method":"result/final","params":{}}\n',
        ),
      ]);
      await parseNdjsonStream(stream, {
        onJson: (obj) => json.push(obj),
        onNonJson: (line) => nonJson.push(line),
      });
      expect(json).toHaveLength(2);
      expect(nonJson).toEqual(["not json"]);
    });

    it("silently drops non-JSON lines when onNonJson is unset", async () => {
      const json: unknown[] = [];
      const stream = Readable.from([
        Buffer.from('{"a":1}\nplain text line\n{"b":2}\n'),
      ]);
      await parseNdjsonStream(stream, { onJson: (obj) => json.push(obj) });
      expect(json).toHaveLength(2);
    });
  });

  describe("SessionHandle wires the pipeline onto stderr", () => {
    it("parses NDJSON lines emitted on the child's stderr", async () => {
      // Capture events via the injected child_process factory.
      const childStdout = JSON.stringify({
        protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        sessionId: "test-session",
        turnId: "t1",
        reply: "done",
        error: null,
        metadata: {},
      });
      const childStderr =
        '{"method":"progress","params":{"message":"thinking"}}\n' +
        '{"method":"tool/started","params":{"name":"bash","args":{}}}\n' +
        '{"method":"tool/completed","params":{"name":"bash","durationMs":12}}\n';

      const seenEvents: unknown[] = [];
      const factory: ChildProcessFactory = (_cmd, _args, _opts) => {
        const child: EventEmitter & {
          stdout: Readable;
          stderr: Readable;
          pid: number;
          exitCode: number | null;
          signalCode: NodeJS.Signals | null;
          kill: () => boolean;
        } = Object.assign(new EventEmitter(), {
          stdout: Readable.from([Buffer.from(childStdout)]),
          stderr: Readable.from([Buffer.from(childStderr)]),
          pid: 99999,
          exitCode: null as number | null,
          signalCode: null as NodeJS.Signals | null,
          kill: () => true,
        });
        setImmediate(() => {
          child.exitCode = 0;
          child.emit("exit", 0, null);
        });
        return child as never;
      };

      const params: SpawnAgentParams = {
        lifecycle: "one-shot",
        sessionId: "test-session",
        _binaryResolver: () => "/usr/bin/false",
        _engineVersionProbe: async () => ({
          version: "0.4.0",
          protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        }),
        runChildProcess: factory,
        display: {
          onEvent: (event) => {
            if (event.type === "notification") {
              seenEvents.push({ method: event.method, params: event.params });
            }
          },
        },
      };

      const handle = await spawnAgent(params);
      for await (const e of handle.submit("hi")) {
        if (e.type === "result" || e.type === "error") break;
      }

      // Issue #2/#6: NDJSON parsed and dispatched.
      expect(seenEvents.length).toBeGreaterThanOrEqual(3);
      const methods = seenEvents.map(
        (e) => (e as { method: string }).method,
      );
      expect(methods).toContain("progress");
      expect(methods).toContain("tool/started");
      expect(methods).toContain("tool/completed");
    });
  });
});
