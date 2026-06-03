/**
 * Tests for argv-builder.ts: assembleArgv()
 *
 * Cases (protocol 0.2.0):
 * (i)   happy path minimal session — exact argv array
 * (ii)  resume mode replaces --fresh with --resume
 * (iii) --host-capabilities flag NOT emitted (removed surface)
 * (iv)  --mcp-config-path flag NOT emitted (removed surface — MCP config
 *       now flows via the AMPLIFIER_MCP_CONFIG env var injected into the
 *       engine's subprocess environment at submit time)
 */
import { describe, it, expect } from "vitest";
import { assembleArgv } from "../src/argv-builder.js";
import type { AssembleArgvInput } from "../src/argv-builder.js";

describe("assembleArgv", () => {
  it("(i) happy path minimal session returns canonical argv", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.2.0",
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
      "0.2.0",
      "-y",
      "hello",
    ]);
  });

  it("(ii) resume mode replaces --fresh with --resume", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.2.0",
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
      protocolVersion: "0.2.0",
    };
    const argv = assembleArgv(input);
    expect(argv).not.toContain("--host-capabilities");
  });

  it("(iv) --mcp-config-path is not emitted (removed surface)", () => {
    // MCP config is now forwarded via the AMPLIFIER_MCP_CONFIG env var
    // injected into the engine's subprocess environment at submit time
    // (or via host_config["mcp"]["configPath"] in the host's config file).
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.2.0",
    };
    const argv = assembleArgv(input);
    expect(argv).not.toContain("--mcp-config-path");
  });

  it("(v) AssembleArgvInput type does not expose mcpConfigPath", () => {
    // Compile-time guardrail: passing mcpConfigPath must be a TypeScript
    // type error. The @ts-expect-error directive asserts that the
    // following line WILL fail type-checking — if a future refactor
    // re-adds the field, this directive becomes a build error.
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.2.0",
      // @ts-expect-error -- mcpConfigPath was removed from AssembleArgvInput.
      mcpConfigPath: "/tmp/x.json",
    };
    const argv = assembleArgv(input);
    expect(argv).not.toContain("--mcp-config-path");
    expect(argv).not.toContain("/tmp/x.json");
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
