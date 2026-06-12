/**
 * Tests for list-models.ts: listModels()
 *
 * The wrapper-side counterpart to the Python `amplifier-agent models list`
 * subcommand. We mock `node:child_process` so we have full control over the
 * spawned subprocess's stdout/stderr/exit-code/timing without invoking a real
 * binary.
 *
 * Cases:
 *  (1) happy path — valid envelope, exit 0
 *  (2) custom binaryPath honored
 *  (3) empty models list (azure-openai case) — not an error
 *  (4) provider error (exit 2) → ListModelsError, exitCode === 2
 *  (5) usage error (exit 1) → ListModelsError, exitCode === 1
 *  (6) timeout → ListModelsError, subprocess killed
 *  (7) malformed JSON → ListModelsError("invalid envelope: …")
 *  (8) wrong schema_version → ListModelsError("invalid envelope: …")
 *  (9) env forwarding — provided env passed to spawn options
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EventEmitter } from "node:events";

// ---------------------------------------------------------------------------
// Mock harness for node:child_process.spawn
// ---------------------------------------------------------------------------

interface FakeChild extends EventEmitter {
  stdout: EventEmitter;
  stderr: EventEmitter;
  kill: ReturnType<typeof vi.fn>;
  pid: number;
}

interface SpawnCall {
  command: string;
  args: readonly string[];
  options: { env?: NodeJS.ProcessEnv } | undefined;
  child: FakeChild;
}

const spawnCalls: SpawnCall[] = [];

function makeChild(): FakeChild {
  const child = new EventEmitter() as FakeChild;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = vi.fn();
  child.pid = 12345;
  return child;
}

vi.mock("node:child_process", () => ({
  spawn: vi.fn(
    (command: string, args: readonly string[], options?: { env?: NodeJS.ProcessEnv }) => {
      const child = makeChild();
      spawnCalls.push({ command, args, options, child });
      return child;
    },
  ),
}));

// ---------------------------------------------------------------------------
// Module under test (imported AFTER the mock so it picks up the fake spawn)
// ---------------------------------------------------------------------------

import { listModels, listAllModels, ListModelsError } from "../src/list-models.js";
import type {
  ListAllModelsEnvelope,
  ModelsListEnvelope,
} from "../src/list-models.js";

beforeEach(() => {
  spawnCalls.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});

/**
 * Drive a fake child through a complete (stdout, stderr, exit) lifecycle.
 * Uses queueMicrotask so the listModels() promise has time to attach listeners.
 */
function driveChild(
  child: FakeChild,
  stdout: string,
  stderr: string,
  exitCode: number | null,
): void {
  queueMicrotask(() => {
    if (stdout.length > 0) child.stdout.emit("data", Buffer.from(stdout, "utf8"));
    if (stderr.length > 0) child.stderr.emit("data", Buffer.from(stderr, "utf8"));
    child.emit("exit", exitCode, null);
  });
}

describe("listModels", () => {
  it("(1) happy path returns parsed envelope and spawns with canonical argv", async () => {
    const envelope: ModelsListEnvelope = {
      schema_version: 1,
      provider: "anthropic",
      fetched_at: "2026-06-10T17:36:53Z",
      models: [
        {
          id: "claude-sonnet-4-5",
          display_name: "Claude Sonnet 4.5",
          context_window: 200000,
          max_output_tokens: 8192,
          capabilities: ["tools", "vision", "thinking"],
          defaults: { temperature: 0.7, max_tokens: 8192 },
        },
        {
          id: "claude-opus-4",
          display_name: "Claude Opus 4",
          context_window: 200000,
          max_output_tokens: 4096,
          capabilities: ["tools", "vision"],
          defaults: {},
        },
      ],
    };
    const promise = listModels({ provider: "anthropic" });
    expect(spawnCalls).toHaveLength(1);
    expect(spawnCalls[0]!.command).toBe("amplifier-agent");
    expect(spawnCalls[0]!.args).toEqual([
      "models",
      "list",
      "--provider",
      "anthropic",
      "--output",
      "json",
    ]);
    driveChild(spawnCalls[0]!.child, JSON.stringify(envelope), "", 0);
    const result = await promise;
    expect(result).toEqual(envelope);
    expect(result.models).toHaveLength(2);
  });

  it("(2) custom binaryPath honored", async () => {
    const envelope: ModelsListEnvelope = {
      schema_version: 1,
      provider: "anthropic",
      fetched_at: "2026-06-10T17:36:53Z",
      models: [],
    };
    const promise = listModels({
      provider: "anthropic",
      binaryPath: "/usr/local/bin/amplifier-agent",
    });
    expect(spawnCalls[0]!.command).toBe("/usr/local/bin/amplifier-agent");
    driveChild(spawnCalls[0]!.child, JSON.stringify(envelope), "", 0);
    await promise;
  });

  it("(3) empty models list (azure-openai case) is not an error", async () => {
    const envelope: ModelsListEnvelope = {
      schema_version: 1,
      provider: "azure-openai",
      fetched_at: "2026-06-10T17:36:53Z",
      models: [],
    };
    const promise = listModels({ provider: "azure-openai" });
    driveChild(spawnCalls[0]!.child, JSON.stringify(envelope), "", 0);
    const result = await promise;
    expect(result.models).toEqual([]);
    expect(result.provider).toBe("azure-openai");
  });

  it("(4) provider error (exit 2) rejects with ListModelsError carrying exitCode and stderr", async () => {
    const promise = listModels({ provider: "anthropic" });
    driveChild(
      spawnCalls[0]!.child,
      "",
      "anthropic: ANTHROPIC_API_KEY not set\n",
      2,
    );
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toMatchObject({
      exitCode: 2,
      stderr: expect.stringContaining("ANTHROPIC_API_KEY not set"),
    });
  });

  it("(5) usage error (exit 1) rejects with ListModelsError, exitCode === 1", async () => {
    const promise = listModels({ provider: "foo" });
    driveChild(spawnCalls[0]!.child, "", "unknown provider: foo\n", 1);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toMatchObject({ exitCode: 1 });
    await expect(promise).rejects.toThrow(/usage error/i);
  });

  it("(6) timeout kills subprocess and rejects with timed-out ListModelsError", async () => {
    const promise = listModels({ provider: "anthropic", timeoutMs: 25 });
    const child = spawnCalls[0]!.child;
    // Deliberately never emit any data; never emit exit. The setTimeout in
    // listModels should fire, call kill(), and reject the promise.
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/timed out/i);
    expect(child.kill).toHaveBeenCalled();
  });

  it("(7) malformed JSON rejects with ListModelsError(invalid envelope)", async () => {
    const promise = listModels({ provider: "anthropic" });
    driveChild(spawnCalls[0]!.child, "this is not json", "", 0);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/invalid envelope/i);
  });

  it("(8) wrong schema_version rejects with ListModelsError(invalid envelope)", async () => {
    const bad = JSON.stringify({
      schema_version: 2,
      provider: "anthropic",
      fetched_at: "2026-06-10T17:36:53Z",
      models: [],
    });
    const promise = listModels({ provider: "anthropic" });
    driveChild(spawnCalls[0]!.child, bad, "", 0);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/invalid envelope|schema_version/i);
  });

  it("(9) env forwarding — supplied env is passed to spawn options", async () => {
    const envelope: ModelsListEnvelope = {
      schema_version: 1,
      provider: "anthropic",
      fetched_at: "2026-06-10T17:36:53Z",
      models: [],
    };
    const promise = listModels({
      provider: "anthropic",
      env: { ANTHROPIC_API_KEY: "test-key", PATH: "/usr/bin" },
    });
    expect(spawnCalls[0]!.options?.env).toEqual({
      ANTHROPIC_API_KEY: "test-key",
      PATH: "/usr/bin",
    });
    driveChild(spawnCalls[0]!.child, JSON.stringify(envelope), "", 0);
    await promise;
  });
});

/**
 * Aggregate-mode counterpart to the listModels block above. Same subprocess
 * driver harness; the only differences are (a) the argv omits --provider,
 * (b) the envelope is the per-provider results shape, and (c) per-provider
 * failures are reported INSIDE the envelope rather than rejecting the
 * promise. The subprocess-level error contract is identical (exit 0/1/2,
 * timeout, malformed JSON).
 */
describe("listAllModels", () => {
  const sampleEnvelope: ListAllModelsEnvelope = {
    schema_version: 1,
    fetched_at: "2026-06-12T18:08:54Z",
    results: [
      {
        provider: "anthropic",
        status: "ok",
        models: [
          {
            id: "claude-opus-4-5",
            display_name: "Claude Opus 4.5",
            context_window: 200000,
            max_output_tokens: 32000,
            capabilities: ["tools", "thinking"],
            defaults: { temperature: 0.7 },
          },
        ],
      },
      {
        provider: "openai",
        status: "credentials_missing",
        models: [],
        error: "OPENAI_API_KEY not set",
      },
      {
        provider: "ollama",
        status: "ok",
        models: [],
      },
      {
        provider: "azure-openai",
        status: "ok",
        models: [],
      },
    ],
  };

  it("(A1) happy path returns aggregate envelope and spawns WITHOUT --provider", async () => {
    const promise = listAllModels();
    expect(spawnCalls).toHaveLength(1);
    expect(spawnCalls[0]!.command).toBe("amplifier-agent");
    // Critical: no --provider in argv. This is the whole point of the function.
    expect(spawnCalls[0]!.args).toEqual(["models", "list", "--output", "json"]);
    expect(spawnCalls[0]!.args).not.toContain("--provider");
    driveChild(spawnCalls[0]!.child, JSON.stringify(sampleEnvelope), "", 0);
    const result = await promise;
    expect(result).toEqual(sampleEnvelope);
    expect(result.results).toHaveLength(4);
  });

  it("(A2) custom binaryPath honored", async () => {
    const promise = listAllModels({ binaryPath: "/usr/local/bin/amplifier-agent" });
    expect(spawnCalls[0]!.command).toBe("/usr/local/bin/amplifier-agent");
    driveChild(spawnCalls[0]!.child, JSON.stringify(sampleEnvelope), "", 0);
    await promise;
  });

  it("(A3) mixed per-provider statuses (ok, credentials_missing, etc.) is NOT an error", async () => {
    // The whole point of aggregate mode: per-provider failures are data, not
    // exceptions. Auth-missing providers return status="credentials_missing"
    // inside the envelope; the promise resolves successfully.
    const promise = listAllModels();
    driveChild(spawnCalls[0]!.child, JSON.stringify(sampleEnvelope), "", 0);
    const result = await promise;
    expect(result.results.find((r) => r.provider === "anthropic")?.status).toBe("ok");
    expect(result.results.find((r) => r.provider === "openai")?.status).toBe(
      "credentials_missing",
    );
    expect(result.results.find((r) => r.provider === "openai")?.error).toContain(
      "OPENAI_API_KEY",
    );
  });

  it("(A4) provider error (exit 2) rejects with ListModelsError carrying exitCode and stderr", async () => {
    const promise = listAllModels();
    driveChild(spawnCalls[0]!.child, "", "engine-level discovery failure\n", 2);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toMatchObject({
      exitCode: 2,
      stderr: expect.stringContaining("discovery failure"),
    });
  });

  it("(A5) usage error (exit 1) rejects with ListModelsError, exitCode === 1", async () => {
    const promise = listAllModels();
    driveChild(spawnCalls[0]!.child, "", "usage: unexpected flag\n", 1);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toMatchObject({ exitCode: 1 });
    await expect(promise).rejects.toThrow(/usage error/i);
  });

  it("(A6) timeout kills subprocess and rejects with timed-out ListModelsError", async () => {
    const promise = listAllModels({ timeoutMs: 25 });
    const child = spawnCalls[0]!.child;
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/timed out/i);
    expect(child.kill).toHaveBeenCalled();
  });

  it("(A7) malformed JSON rejects with ListModelsError(invalid envelope)", async () => {
    const promise = listAllModels();
    driveChild(spawnCalls[0]!.child, "this is not json", "", 0);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/invalid envelope/i);
  });

  it("(A8) wrong schema_version rejects with ListModelsError(invalid envelope)", async () => {
    const bad = JSON.stringify({
      schema_version: 2,
      fetched_at: "2026-06-12T18:08:54Z",
      results: [],
    });
    const promise = listAllModels();
    driveChild(spawnCalls[0]!.child, bad, "", 0);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/invalid envelope|schema_version/i);
  });

  it("(A9) env forwarding — supplied env is passed to spawn options", async () => {
    const promise = listAllModels({
      env: {
        ANTHROPIC_API_KEY: "anthro-key",
        OPENAI_API_KEY: "openai-key",
        PATH: "/usr/bin",
      },
    });
    expect(spawnCalls[0]!.options?.env).toEqual({
      ANTHROPIC_API_KEY: "anthro-key",
      OPENAI_API_KEY: "openai-key",
      PATH: "/usr/bin",
    });
    driveChild(spawnCalls[0]!.child, JSON.stringify(sampleEnvelope), "", 0);
    await promise;
  });

  it("(A10) zero-args call (no params) works and inherits process.env", async () => {
    // Documented entry point: listAllModels() with no argument is valid.
    const promise = listAllModels();
    expect(spawnCalls).toHaveLength(1);
    expect(spawnCalls[0]!.options?.env).toBe(process.env);
    driveChild(spawnCalls[0]!.child, JSON.stringify(sampleEnvelope), "", 0);
    await promise;
  });

  it("(A11) missing results[] field rejects with ListModelsError(invalid envelope)", async () => {
    // Sanity check on aggregate validator: results must be an array.
    const bad = JSON.stringify({
      schema_version: 1,
      fetched_at: "2026-06-12T18:08:54Z",
      // results: undefined — wrong
    });
    const promise = listAllModels();
    driveChild(spawnCalls[0]!.child, bad, "", 0);
    await expect(promise).rejects.toBeInstanceOf(ListModelsError);
    await expect(promise).rejects.toThrow(/results must be an array/i);
  });
});
