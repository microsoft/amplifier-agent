/**
 * Tests for spawn.ts: resolveBinaryPath() and buildEnv()
 *
 * TDD bullets (11b):
 * - resolveBinaryPath returns AMPLIFIER_AGENT_BIN value when set
 * - buildEnv drops disallowed variables and merges extras over allowlist
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  resolveBinaryPath,
  buildEnv,
  probeEngineVersion,
  DEFAULT_ALLOWLIST,
} from "../src/spawn.js";
import { AaaError } from "../src/session.js";

describe("AaaError v0.1.0 fields", () => {
  it("accepts classification and severity via opts", () => {
    const err = new AaaError("approval_timeout", "timed out", {
      classification: "approval",
      severity: "error",
    });
    expect(err.code).toBe("approval_timeout");
    expect(err.classification).toBe("approval");
    expect(err.severity).toBe("error");
  });

  it("leaves classification/severity/correlationId undefined when opts not provided", () => {
    const err = new AaaError("internal", "oops");
    expect(err.classification).toBeUndefined();
    expect(err.severity).toBeUndefined();
    expect(err.correlationId).toBeUndefined();
  });
});

describe("resolveBinaryPath", () => {
  it("returns AMPLIFIER_AGENT_BIN value when env var is set (and path exists)", () => {
    // Use /bin/sh as a guaranteed-existing binary for the test
    const result = resolveBinaryPath({ env: { AMPLIFIER_AGENT_BIN: "/bin/sh" } });
    expect(result).toBe("/bin/sh");
  });
});

describe("buildEnv", () => {
  it("drops disallowed variables and keeps allowed ones", () => {
    const processEnv = {
      PATH: "/usr/bin",
      HOME: "/home/user",
      SECRET_TOKEN: "should-be-dropped",
      CUSTOM_VAR: "also-dropped",
    };
    const result = buildEnv({ processEnv, allowlist: DEFAULT_ALLOWLIST });
    expect(result["PATH"]).toBe("/usr/bin");
    expect(result["HOME"]).toBe("/home/user");
    expect(result["SECRET_TOKEN"]).toBeUndefined();
    expect(result["CUSTOM_VAR"]).toBeUndefined();
  });

  it("merges extras over the allowlist", () => {
    const processEnv = {
      PATH: "/usr/bin",
      HOME: "/home/user",
    };
    const extra = { CUSTOM_EXTRA: "extra-value" };
    const result = buildEnv({ processEnv, allowlist: DEFAULT_ALLOWLIST, extra });
    expect(result["CUSTOM_EXTRA"]).toBe("extra-value");
    expect(result["PATH"]).toBe("/usr/bin");
  });

  it("keeps AMPLIFIER_ prefixed variables", () => {
    const processEnv = {
      PATH: "/usr/bin",
      AMPLIFIER_AGENT_BIN: "/custom/agent",
      AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW: "1",
    };
    const result = buildEnv({ processEnv, allowlist: DEFAULT_ALLOWLIST });
    expect(result["AMPLIFIER_AGENT_BIN"]).toBe("/custom/agent");
    expect(result["AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW"]).toBe("1");
  });

  it("keeps LC_ prefixed variables", () => {
    const processEnv = {
      PATH: "/usr/bin",
      LC_ALL: "en_US.UTF-8",
      LC_CTYPE: "UTF-8",
    };
    const result = buildEnv({ processEnv, allowlist: DEFAULT_ALLOWLIST });
    expect(result["LC_ALL"]).toBe("en_US.UTF-8");
    expect(result["LC_CTYPE"]).toBe("UTF-8");
  });
});

describe("probeEngineVersion (SC-7)", () => {
  it("probeEngineVersion is an async function returning a Promise", async () => {
    const result = probeEngineVersion("/nonexistent-binary", {});
    expect(result).toBeInstanceOf(Promise);
    await result.catch(() => {});
  });
});

describe("BLOCKED_ENV_KEYS validation (SC-3)", () => {
  it("throws AaaError(env_injection_rejected) when PYTHONPATH is in extra", () => {
    expect(() =>
      buildEnv({
        processEnv: { PATH: "/usr/bin" },
        allowlist: DEFAULT_ALLOWLIST,
        extra: { PYTHONPATH: "/evil" },
      }),
    ).toThrow(
      expect.objectContaining({
        code: "env_injection_rejected",
        classification: "protocol",
        severity: "error",
      }),
    );
  });

  it("throws AaaError(env_injection_rejected) when LD_PRELOAD is in extra", () => {
    expect(() =>
      buildEnv({
        processEnv: { PATH: "/usr/bin" },
        allowlist: DEFAULT_ALLOWLIST,
        extra: { LD_PRELOAD: "/evil.so" },
      }),
    ).toThrow(
      expect.objectContaining({
        code: "env_injection_rejected",
        classification: "protocol",
        severity: "error",
      }),
    );
  });

  it("does NOT throw for safe extra keys", () => {
    const result = buildEnv({
      processEnv: { PATH: "/usr/bin" },
      allowlist: DEFAULT_ALLOWLIST,
      extra: { CUSTOM_SAFE: "ok" },
    });
    expect(result["CUSTOM_SAFE"]).toBe("ok");
  });
});
