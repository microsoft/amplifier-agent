/**
 * Test for Issue #10 — wire approval API to engine -y/-n flags.
 *
 * Previously, SpawnAgentParams.approval threw AaaError(
 * approval_not_supported_in_v1) whenever set (because it required the
 * mid-turn onRequest callback that v1 doesn't support).
 *
 * After this PR, SpawnAgentParams.approval also accepts a `mode` shape
 * that maps to engine argv:
 *   mode: 'yes'    -> -y (always allow)
 *   mode: 'no'     -> -n (always deny)
 *   mode: 'prompt' -> emit nothing (engine falls back to
 *                     host_config.approval.mode or its bundle/TTY default)
 *
 * The legacy onRequest form still throws — there's no mid-turn channel.
 */

import { describe, it, expect } from "vitest";
import { EventEmitter } from "node:events";
import { Readable } from "node:stream";

import {
  spawnAgent,
  AaaError,
  assembleArgv,
  PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
} from "../src/index.js";
import type { SpawnAgentParams, ChildProcessFactory } from "../src/index.js";

function captureArgv(): {
  factory: ChildProcessFactory;
  getArgs: () => readonly string[];
} {
  let captured: readonly string[] = [];
  const envelope = JSON.stringify({
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    sessionId: "test-session",
    turnId: "t1",
    reply: "ok",
    error: null,
    metadata: {},
  });
  const factory: ChildProcessFactory = (_cmd, args, _opts) => {
    captured = args;
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
  return { factory, getArgs: () => captured };
}

const baseParams: Omit<SpawnAgentParams, "runChildProcess"> = {
  lifecycle: "one-shot",
  sessionId: "test-session",
  _binaryResolver: () => "/usr/bin/false",
  _engineVersionProbe: async () => ({
    version: "0.4.0",
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  }),
};

async function runWithApproval(
  approval: SpawnAgentParams["approval"],
): Promise<readonly string[]> {
  const { factory, getArgs } = captureArgv();
  const params: SpawnAgentParams = {
    ...baseParams,
    runChildProcess: factory,
    ...(approval !== undefined ? { approval } : {}),
  };
  const handle = await spawnAgent(params);
  for await (const e of handle.submit("hi")) {
    if (e.type === "result" || e.type === "error") break;
  }
  return getArgs();
}

describe("Issue #10 — approval API maps to engine -y/-n argv", () => {
  describe("assembleArgv branch coverage", () => {
    it("emits -y for approvalMode='yes'", () => {
      const argv = assembleArgv({
        sessionId: "s",
        prompt: "p",
        protocolVersion: "0.3.0",
        approvalMode: "yes",
      });
      expect(argv).toContain("-y");
      expect(argv).not.toContain("-n");
    });

    it("emits -n for approvalMode='no'", () => {
      const argv = assembleArgv({
        sessionId: "s",
        prompt: "p",
        protocolVersion: "0.3.0",
        approvalMode: "no",
      });
      expect(argv).toContain("-n");
      expect(argv).not.toContain("-y");
    });

    it("emits no approval flag for approvalMode='prompt'", () => {
      const argv = assembleArgv({
        sessionId: "s",
        prompt: "p",
        protocolVersion: "0.3.0",
        approvalMode: "prompt",
      });
      expect(argv).not.toContain("-y");
      expect(argv).not.toContain("-n");
    });

    it("emits -y by default (no approvalMode) for backward compat", () => {
      const argv = assembleArgv({
        sessionId: "s",
        prompt: "p",
        protocolVersion: "0.3.0",
      });
      expect(argv).toContain("-y");
    });
  });

  describe("SpawnAgentParams.approval threading", () => {
    it("approval: {mode:'yes'} forwards -y", async () => {
      const args = await runWithApproval({ mode: "yes" });
      expect(args).toContain("-y");
      expect(args).not.toContain("-n");
    });

    it("approval: {mode:'no'} forwards -n", async () => {
      const args = await runWithApproval({ mode: "no" });
      expect(args).toContain("-n");
      expect(args).not.toContain("-y");
    });

    it("approval: {mode:'prompt'} forwards neither -y nor -n", async () => {
      const args = await runWithApproval({ mode: "prompt" });
      expect(args).not.toContain("-y");
      expect(args).not.toContain("-n");
    });

    it("legacy approval: {onRequest, timeoutMs} still throws approval_not_supported_in_v1", async () => {
      await expect(
        spawnAgent({
          ...baseParams,
          approval: {
            onRequest: async () => ({ decision: "deny" }),
            timeoutMs: 1000,
          },
        }),
      ).rejects.toMatchObject({
        name: "AaaError",
        code: "approval_not_supported_in_v1",
      });
    });

    it("rejects unknown mode strings", async () => {
      await expect(
        spawnAgent({
          ...baseParams,
          // @ts-expect-error — testing the runtime guard for bad shape
          approval: { mode: "always" },
        }),
      ).rejects.toThrow(AaaError);
    });
  });
});
