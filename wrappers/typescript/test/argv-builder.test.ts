/**
 * Tests for argv-builder.ts: assembleArgv()
 *
 * TDD cases (task-5 / protocol 0.2.0):
 * (i) happy path minimal session — exact argv array
 * (ii) resume mode replaces --fresh with --resume
 * (iii) --host-capabilities threaded as JSON string and parseable
 * (iv) --mcp-config-path threaded as plain path
 */
import { describe, it, expect } from "vitest";
import { assembleArgv } from "../src/argv-builder.js";
import type { AssembleArgvInput } from "../src/argv-builder.js";

describe("assembleArgv", () => {
  it("(i) happy path minimal session returns canonical argv", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
    };
    const argv = assembleArgv(input);
    expect(argv).toEqual([
      "run",
      "--session-id",
      "sid",
      "--fresh",
      "--output",
      "json",
      "--protocol-version",
      "0.1.0",
      "-y",
      "hello",
    ]);
  });

  it("(ii) resume mode replaces --fresh with --resume", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      resume: true,
    };
    const argv = assembleArgv(input);
    expect(argv).toContain("--resume");
    expect(argv).not.toContain("--fresh");
  });

  it("(iii) --host-capabilities is not emitted (removed surface)", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
    };
    const argv = assembleArgv(input);
    expect(argv).not.toContain("--host-capabilities");
  });

  it("(iv) --mcp-config-path threaded as plain path", () => {
    const configPath = "/tmp/amplifier-agent/sess-abc/mcp.json";
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      mcpConfigPath: configPath,
    };
    const argv = assembleArgv(input);
    const idx = argv.indexOf("--mcp-config-path");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(argv[idx + 1]).toBe(configPath);
  });

  it("(removal) AssembleArgvInput does not expose hostCapabilities", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
    };
    const argv = assembleArgv(input);
    expect(argv.filter((a) => a.includes("host"))).toEqual([]);
  });
});
