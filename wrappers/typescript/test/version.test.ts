/**
 * Tests for version.ts: checkProtocolVersion()
 *
 * TDD bullets (11b):
 * - match → ok=true
 * - mismatch → ok=false with code='protocol_version_mismatch' and remediation matches /install|allow-protocol-skew/i
 * - allowSkew=true → ok=true even on mismatch
 */
import { describe, it, expect } from "vitest";
import { checkProtocolVersion } from "../src/version.js";

const WRAPPER_VERSION = "0.1.0";

describe("checkProtocolVersion", () => {
  it("returns ok=true when wrapper and engine versions match", () => {
    const result = checkProtocolVersion({
      wrapper: WRAPPER_VERSION,
      engine: WRAPPER_VERSION,
    });
    expect(result.ok).toBe(true);
  });

  it("returns ok=false with code and remediation when versions mismatch", () => {
    const result = checkProtocolVersion({
      wrapper: WRAPPER_VERSION,
      engine: "2026-04-aaa-v0",
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("protocol_version_mismatch");
      expect(result.remediation).toMatch(/install|allow-protocol-skew/i);
    }
  });

  it("returns ok=true when allowSkew=true even on mismatch", () => {
    const result = checkProtocolVersion({
      wrapper: WRAPPER_VERSION,
      engine: "2026-04-aaa-v0",
      allowSkew: true,
    });
    expect(result.ok).toBe(true);
  });
});
