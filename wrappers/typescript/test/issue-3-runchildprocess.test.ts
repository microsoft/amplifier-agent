/**
 * Test for Issue #3 — SpawnAgentParams.runChildProcess injection point.
 *
 * The wrapper currently calls `child_process.spawn` directly inside
 * SessionHandle.makeIterable(), giving consumers no seam to substitute the
 * subprocess factory for sandboxing, testing, or process isolation.
 *
 * This test pins the contract: when `runChildProcess` is set, the wrapper
 * uses the injected factory instead of `child_process.spawn`.
 */

import { describe, it, expect } from "vitest";
import { EventEmitter } from "node:events";
import { Readable } from "node:stream";

import { spawnAgent, PROTOCOL_VERSION_REQUIRED_BY_WRAPPER } from "../src/index.js";
import type { SpawnAgentParams, ChildProcessFactory } from "../src/index.js";

/** Build a minimal fake ChildProcess that emits a canned stdout envelope. */
function fakeChild(stdoutJson: string): EventEmitter {
  const child: EventEmitter & {
    stdout: Readable;
    stderr: Readable;
    pid: number;
    exitCode: number | null;
    signalCode: NodeJS.Signals | null;
    kill: (sig?: NodeJS.Signals) => boolean;
  } = Object.assign(new EventEmitter(), {
    stdout: Readable.from([Buffer.from(stdoutJson)]),
    stderr: Readable.from([]),
    pid: 99999,
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    kill: () => true,
  });
  // Fire exit on next tick so the iterator can collect.
  setImmediate(() => {
    child.exitCode = 0;
    child.emit("exit", 0, null);
  });
  return child;
}

const baseParams: SpawnAgentParams = {
  lifecycle: "one-shot",
  sessionId: "test-session",
  _binaryResolver: () => "/usr/bin/false",
  _engineVersionProbe: async () => ({
    version: "0.4.0",
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  }),
};

describe("Issue #3 — runChildProcess injection point", () => {
  it("uses the injected factory instead of child_process.spawn when provided", async () => {
    let factoryCalled = false;
    let capturedCommand = "";
    let capturedArgs: string[] = [];

    const envelope = JSON.stringify({
      protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
      sessionId: "test-session",
      turnId: "t1",
      reply: "hello",
      error: null,
      metadata: {},
    });

    const factory: ChildProcessFactory = (cmd, args, _opts) => {
      factoryCalled = true;
      capturedCommand = cmd;
      capturedArgs = args;
      // Cast: the test fake satisfies the surface SessionHandle uses.
      return fakeChild(envelope) as never;
    };

    const handle = await spawnAgent({ ...baseParams, runChildProcess: factory });
    const events: string[] = [];
    for await (const e of handle.submit("hi")) {
      events.push(e.type);
      if (e.type === "result" || e.type === "error") break;
    }
    expect(factoryCalled).toBe(true);
    expect(capturedCommand).toBe("/usr/bin/false");
    expect(capturedArgs).toContain("run");
    expect(events).toContain("init");
    expect(events).toContain("result");
  });
});
