/**
 * Tests for argv-builder.ts: assembleArgv()
 *
 * TDD cases (task-5):
 * (i) happy path minimal session — exact argv array
 * (ii) resume mode replaces --fresh with --resume
 * (iii) --host-capabilities threaded as JSON string and parseable
 * (iv) --mcp-servers threaded as inline JSON when no env spill
 * (v) --mcp-servers @path threaded when caller pre-spilled
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

  it("(iii) --host-capabilities threaded as JSON string and parseable", () => {
    const caps = { fs: { read: true }, net: false };
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      hostCapabilities: caps,
    };
    const argv = assembleArgv(input);
    const idx = argv.indexOf("--host-capabilities");
    expect(idx).toBeGreaterThanOrEqual(0);
    const jsonArg = argv[idx + 1];
    expect(typeof jsonArg).toBe("string");
    expect(JSON.parse(jsonArg as string)).toEqual(caps);
  });

  it("(iv) --mcp-servers threaded as inline JSON when no env spill", () => {
    const inlineJson = '{"servers":[{"id":"a","command":"foo"}]}';
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      mcpServersFlag: inlineJson,
    };
    const argv = assembleArgv(input);
    const idx = argv.indexOf("--mcp-servers");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(argv[idx + 1]).toBe(inlineJson);
  });

  it("(v) --mcp-servers @path threaded when caller pre-spilled", () => {
    const spilled = "@/tmp/aaa-mcp-servers-abc.json";
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      mcpServersFlag: spilled,
    };
    const argv = assembleArgv(input);
    const idx = argv.indexOf("--mcp-servers");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(argv[idx + 1]).toBe(spilled);
  });
});
