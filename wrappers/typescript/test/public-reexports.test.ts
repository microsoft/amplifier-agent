/**
 * Test for Issue #5 — internal helpers must be re-exported from index.ts.
 *
 * Consumer of v0.5.0 reported that internal helpers (assembleArgv,
 * resolveMcpConfigPath, buildEnv, Transport, etc.) are not re-exported from
 * the package entry point and must be reached via deep imports into private
 * paths. This RED test imports them from the package root and asserts that
 * the symbols are present at runtime.
 */

import { describe, it, expect } from "vitest";

import * as pkg from "../src/index.js";

describe("Issue #5 — public re-exports from index.ts", () => {
  it("re-exports assembleArgv (argv-builder)", () => {
    expect(typeof pkg.assembleArgv).toBe("function");
  });

  it("re-exports resolveMcpConfigPath + cleanupSpillFile (mcp-spill)", () => {
    expect(typeof pkg.resolveMcpConfigPath).toBe("function");
    expect(typeof pkg.cleanupSpillFile).toBe("function");
  });

  it("re-exports buildEnv, resolveBinaryPath, DEFAULT_ALLOWLIST, BLOCKED_ENV_KEYS, probeEngineVersion (spawn)", () => {
    expect(typeof pkg.buildEnv).toBe("function");
    expect(typeof pkg.resolveBinaryPath).toBe("function");
    expect(Array.isArray(pkg.DEFAULT_ALLOWLIST)).toBe(true);
    expect(pkg.BLOCKED_ENV_KEYS instanceof Set).toBe(true);
    expect(typeof pkg.probeEngineVersion).toBe("function");
  });

  it("re-exports Transport (transport)", () => {
    expect(typeof pkg.Transport).toBe("function"); // class is typeof function
  });

  it("re-exports checkProtocolVersion (version)", () => {
    expect(typeof pkg.checkProtocolVersion).toBe("function");
  });

  it("re-exports parseRunOutput + STDERR_TAIL_BYTES (run-output-parser)", () => {
    expect(typeof pkg.parseRunOutput).toBe("function");
    expect(typeof pkg.STDERR_TAIL_BYTES).toBe("number");
  });

  it("re-exports makeApprovalHandler (approval)", () => {
    expect(typeof pkg.makeApprovalHandler).toBe("function");
  });
});
