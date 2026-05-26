/**
 * Tests for run-output-parser.ts: parseRunOutput() per §4.1 + §4.4 (SC-D).
 *
 * Six cases exercise the precedence rule from the amendment:
 *   Rule 1 — envelope parseable: envelope wins, exit code is informational.
 *     (1a) valid envelope, error=null, exit 0  → result event with reply text
 *     (1b) valid envelope, error=null, exit 1  → result event (envelope wins)
 *     (1c) valid envelope, error populated     → error event from envelope fields
 *   Rule 2 — envelope absent / unparseable: synthesize from exit code + stderr.
 *     (2a) exit 0 + empty stdout               → envelope_missing / protocol
 *     (2b) non-zero exit + empty stdout        → engine_exit_<N> / engine
 *     (2c) partial/truncated JSON              → engine_exit_<N> / engine (rule 2)
 */
import { describe, it, expect } from "vitest";
import { parseRunOutput } from "../src/run-output-parser.js";
import type { SubprocessOutcome } from "../src/run-output-parser.js";

/** Helper to build a valid §4.1 envelope with overrides. */
function makeEnvelope(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const base: Record<string, unknown> = {
    protocolVersion: "0.1.0",
    sessionId: "sess-abc-001",
    turnId: "turn-1",
    reply: "It is 2:15pm Pacific time.",
    error: null,
    metadata: {
      tokensIn: 1247,
      tokensOut: 89,
      durationMs: 1832,
      bundleDigest: "sha256:7f3a9e2b4c5d6e8f",
      engineVersion: "0.2.0",
      protocolVersion: "0.1.0",
      correlationId: "01HXYZ123ABC456DEF789",
    },
  };
  return { ...base, ...overrides };
}

describe("parseRunOutput — §4.1 envelope + SC-D precedence", () => {
  it("(1a) valid envelope with error=null and exit 0 yields result event", () => {
    const env = makeEnvelope({ reply: "hello world" });
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(env) + "\n",
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("result");
    if (ev.type === "result") {
      expect(ev.text).toBe("hello world");
    }
  });

  it("(1b) valid envelope with error=null and exit 1 still yields result (envelope wins)", () => {
    // Per §4.4 rule 1: the envelope is authoritative; exit code is informational.
    const env = makeEnvelope({ reply: "envelope-wins" });
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(env),
      stderr: "some stderr noise\n",
      exitCode: 1,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("result");
    if (ev.type === "result") {
      expect(ev.text).toBe("envelope-wins");
    }
  });

  it("(1c) valid envelope with populated error yields error event from envelope", () => {
    const env = makeEnvelope({
      reply: "",
      error: {
        code: "approval_translation_failed",
        classification: "approval",
        severity: "error",
        correlationId: "01HXYZ123ABC456DEF789",
        message:
          "failed to translate ApprovalRequest to bundle hook shape: unknown approval action 'review'",
        stderrTail: "Traceback (most recent call last):\n  ...",
      },
      metadata: {
        tokensIn: 0,
        tokensOut: 0,
        durationMs: 247,
        bundleDigest: "sha256:7f3a9e2b",
        engineVersion: "0.2.0",
        protocolVersion: "0.1.0",
        correlationId: "01HXYZ123ABC456DEF789",
      },
    });
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(env),
      stderr: "ignored when envelope provides stderrTail",
      exitCode: 3,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("error");
    if (ev.type === "error") {
      expect(ev.code).toBe("approval_translation_failed");
      expect(ev.classification).toBe("approval");
      expect(ev.severity).toBe("error");
      expect(ev.correlationId).toBe("01HXYZ123ABC456DEF789");
      expect(ev.message).toContain("failed to translate");
      expect(ev.stderrTail).toContain("Traceback");
      expect(ev.retryable).toBe(false);
    }
  });

  it("(2a) exit 0 + empty stdout yields envelope_missing protocol error", () => {
    const outcome: SubprocessOutcome = {
      stdout: "",
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("error");
    if (ev.type === "error") {
      expect(ev.code).toBe("envelope_missing");
      expect(ev.classification).toBe("protocol");
      expect(ev.severity).toBe("error");
      expect(ev.retryable).toBe(false);
      expect(ev.message).toMatch(/envelope/i);
    }
  });

  it("(2b) non-zero exit + empty stdout yields engine_exit_<N> engine error with stderrTail", () => {
    const stderr = "amplifier-agent: provider initialization failed\nstack trace...\n";
    const outcome: SubprocessOutcome = {
      stdout: "",
      stderr,
      exitCode: 137,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("error");
    if (ev.type === "error") {
      expect(ev.code).toBe("engine_exit_137");
      expect(ev.classification).toBe("engine");
      expect(ev.severity).toBe("error");
      expect(ev.retryable).toBe(false);
      expect(ev.stderrTail).toBe(stderr);
    }
  });

  it("(2c) partial/truncated JSON falls to rule 2 (engine_exit_<N>, classification engine)", () => {
    // Per §4.4 rule 2: belt-and-suspenders — partial JSON is NOT half-parsed.
    const outcome: SubprocessOutcome = {
      stdout: '{"protocolVersion":"0.1.0","sessionId":"sess-abc","turnId":"turn-1","reply":"hi"',
      stderr: "engine died mid-write\n",
      exitCode: 1,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("error");
    if (ev.type === "error") {
      expect(ev.code).toBe("engine_exit_1");
      expect(ev.classification).toBe("engine");
      expect(ev.severity).toBe("error");
      expect(ev.retryable).toBe(false);
    }
  });

  it("truncates stderrTail to 4096 chars on synthesized engine errors", () => {
    // stderr longer than 4096 bytes — only the last 4096 should be kept.
    const long = "X".repeat(5000) + "TAIL_MARKER";
    const outcome: SubprocessOutcome = {
      stdout: "",
      stderr: long,
      exitCode: 2,
    };
    const ev = parseRunOutput(outcome);
    expect(ev.type).toBe("error");
    if (ev.type === "error") {
      expect(ev.stderrTail).toBeDefined();
      expect(ev.stderrTail!.length).toBe(4096);
      // Last bytes must be preserved (we keep the *tail*).
      expect(ev.stderrTail!.endsWith("TAIL_MARKER")).toBe(true);
    }
  });
});
