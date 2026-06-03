/**
 * Test for Issue #1 — surface the engine's --config flag via
 * SpawnAgentParams.configPath.
 *
 * The engine accepts --config <path> (single_turn.py:483) to point at a
 * host_config JSON/YAML file. The wrapper had no field for this; consumers
 * had to fall back to AMPLIFIER_AGENT_CONFIG in env.extra.
 *
 * This test pins:
 *   1. assembleArgv emits --config <path> when configPath is set.
 *   2. assembleArgv omits --config when configPath is unset.
 *   3. End-to-end: SpawnAgentParams.configPath threads into the argv.
 */

import { describe, it, expect } from "vitest";
import { EventEmitter } from "node:events";
import { Readable } from "node:stream";

import {
  assembleArgv,
  spawnAgent,
  PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
} from "../src/index.js";
import type { SpawnAgentParams, ChildProcessFactory } from "../src/index.js";

describe("Issue #1 — SpawnAgentParams.configPath threads to --config argv", () => {
  it("assembleArgv emits --config <path> when configPath is set", () => {
    const argv = assembleArgv({
      sessionId: "s1",
      prompt: "hello",
      protocolVersion: "0.3.0",
      configPath: "/etc/amplifier/host_config.json",
    });
    const idx = argv.indexOf("--config");
    expect(idx).toBeGreaterThan(0);
    expect(argv[idx + 1]).toBe("/etc/amplifier/host_config.json");
  });

  it("assembleArgv omits --config when configPath is unset", () => {
    const argv = assembleArgv({
      sessionId: "s1",
      prompt: "hello",
      protocolVersion: "0.3.0",
    });
    expect(argv).not.toContain("--config");
  });

  it("spawnAgent threads configPath into the child subprocess argv", async () => {
    let capturedArgs: readonly string[] = [];
    const envelope = JSON.stringify({
      protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
      sessionId: "test-session",
      turnId: "t1",
      reply: "ok",
      error: null,
      metadata: {},
    });
    const factory: ChildProcessFactory = (_cmd, args, _opts) => {
      capturedArgs = args;
      const child: EventEmitter & {
        stdout: Readable;
        stderr: Readable;
        pid: number;
        exitCode: number | null;
        signalCode: NodeJS.Signals | null;
        kill: () => boolean;
      } = Object.assign(new EventEmitter(), {
        stdout: Readable.from([Buffer.from(envelope)]),
        stderr: Readable.from([]),
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
      configPath: "/etc/amplifier/host_config.yaml",
    };

    const handle = await spawnAgent(params);
    for await (const e of handle.submit("hi")) {
      if (e.type === "result" || e.type === "error") break;
    }

    const idx = capturedArgs.indexOf("--config");
    expect(idx).toBeGreaterThan(0);
    expect(capturedArgs[idx + 1]).toBe("/etc/amplifier/host_config.yaml");
  });
});
