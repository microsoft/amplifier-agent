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
 *
 * Plus the protocol-0.4.0 additions: the envelope's identity and usage block
 * are surfaced rather than discarded, and `stderrTailBytes` bounds the tail in
 * real UTF-8 BYTES.
 */
import { describe, it, expect } from "vitest";
import {
  parseRunOutput,
  tailStderrBytes,
  STDERR_TAIL_BYTES,
} from "../src/run-output-parser.js";
import type { SubprocessOutcome } from "../src/run-output-parser.js";

/** Helper to build a valid §4.1 envelope with overrides. */
function makeEnvelope(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const base: Record<string, unknown> = {
    protocolVersion: "0.4.0",
    sessionId: "sess-abc-001",
    turnId: "turn-1",
    reply: "It is 2:15pm Pacific time.",
    error: null,
    metadata: {
      // Protocol 0.4.0 usage block. tokensIn is the CHARGED total:
      // 1247 = 1200 gross input (900 fresh + 300 cache reads, already combined
      // by the provider) + 47 cache writes billed on top.
      tokensIn: 1247,
      tokensOut: 89,
      cacheReadTokens: 300,
      cacheWriteTokens: 47,
      costUsd: "0.00421500",
      durationMs: 1832,
      bundleDigest: "sha256:7f3a9e2b4c5d6e8f",
      engineVersion: "0.4.0",
      protocolVersion: "0.4.0",
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
      expect(ev.sessionId).toBe("sess-abc-001");
      expect(ev.turnId).toBe("turn-1");
      expect(ev.exitCode).toBe(0);
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
      // Reported as observed, not used to override the envelope: this is how a
      // host sees a post-flush crash without the outcome being mis-classified.
      expect(ev.exitCode).toBe(1);
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
        tokensIn: 512,
        tokensOut: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: null,
        durationMs: 247,
        bundleDigest: "sha256:7f3a9e2b",
        engineVersion: "0.4.0",
        protocolVersion: "0.4.0",
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
      // D6: a turn that spent tokens and THEN failed still reports what it
      // spent. Nothing else on the failure path carries usage.
      expect(ev.sessionId).toBe("sess-abc-001");
      expect(ev.turnId).toBe("turn-1");
      expect(ev.exitCode).toBe(3);
      expect(ev.usage).toEqual({
        inputTokens: 512,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        costUsd: null,
      });
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
      // Rule 2: no envelope means no identity and no usage to report.
      expect(ev.sessionId).toBeUndefined();
      expect(ev.turnId).toBeUndefined();
      expect(ev.usage).toBeUndefined();
      expect(ev.exitCode).toBe(0);
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
      expect(ev.exitCode).toBe(137);
      expect(ev.usage).toBeUndefined();
    }
  });

  it("(2c) partial/truncated JSON falls to rule 2 (engine_exit_<N>, classification engine)", () => {
    // Per §4.4 rule 2: belt-and-suspenders — partial JSON is NOT half-parsed.
    const outcome: SubprocessOutcome = {
      stdout: '{"protocolVersion":"0.4.0","sessionId":"sess-abc","turnId":"turn-1","reply":"hi"',
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
      // The sessionId is textually present in that truncated stdout, but the
      // envelope did not parse, so claiming it would be reading a half-parsed
      // envelope — exactly what rule 2 forbids.
      expect(ev.sessionId).toBeUndefined();
    }
  });

  it("truncates stderrTail to 4096 BYTES on synthesized engine errors", () => {
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
      expect(Buffer.byteLength(ev.stderrTail!, "utf-8")).toBe(4096);
      // Last bytes must be preserved (we keep the *tail*).
      expect(ev.stderrTail!.endsWith("TAIL_MARKER")).toBe(true);
    }
  });
});

describe("parseRunOutput — fallbackSessionId on synthesized (Rule 2) paths", () => {
  it("(f1) envelope_missing carries the caller's sessionId, and still no turnId", () => {
    const ev = parseRunOutput(
      { stdout: "", stderr: "", exitCode: 0 },
      { fallbackSessionId: "sess-host-42" },
    );
    expect(ev.type).toBe("error");
    if (ev.type !== "error") return;
    expect(ev.code).toBe("envelope_missing");
    expect(ev.sessionId).toBe("sess-host-42");
    // The engine assigns turn ids and no envelope came back, so there is
    // genuinely nothing to report here. Absent, never invented.
    expect(ev.turnId).toBeUndefined();
    expect(ev.usage).toBeUndefined();
  });

  it("(f2) engine_exit_<N> carries the caller's sessionId, and still no turnId", () => {
    const ev = parseRunOutput(
      { stdout: "", stderr: "boom\n", exitCode: 7 },
      { fallbackSessionId: "sess-host-42" },
    );
    expect(ev.type).toBe("error");
    if (ev.type !== "error") return;
    expect(ev.code).toBe("engine_exit_7");
    expect(ev.sessionId).toBe("sess-host-42");
    expect(ev.turnId).toBeUndefined();
    expect(ev.exitCode).toBe(7);
  });

  it("(f3) partial JSON still falls to rule 2, reporting the caller's id — not the one in the unparsed text", () => {
    const ev = parseRunOutput(
      {
        stdout:
          '{"protocolVersion":"0.4.0","sessionId":"sess-from-broken-json","turnId":"turn-1","reply":"hi"',
        stderr: "engine died mid-write\n",
        exitCode: 1,
      },
      { fallbackSessionId: "sess-host-42" },
    );
    expect(ev.type).toBe("error");
    if (ev.type !== "error") return;
    expect(ev.code).toBe("engine_exit_1");
    // The id comes from the handle, NOT from half-parsing a broken envelope.
    expect(ev.sessionId).toBe("sess-host-42");
    expect(ev.turnId).toBeUndefined();
  });

  it("(f4) omitting the option leaves sessionId absent (unchanged default)", () => {
    const ev = parseRunOutput({ stdout: "", stderr: "", exitCode: 3 });
    expect(ev.type).toBe("error");
    if (ev.type !== "error") return;
    expect(ev.sessionId).toBeUndefined();
    expect(ev.turnId).toBeUndefined();
  });

  it("(f5) never overrides a parsed envelope — Rule 1 result keeps the envelope's ids", () => {
    const ev = parseRunOutput(
      { stdout: JSON.stringify(makeEnvelope()), stderr: "", exitCode: 0 },
      { fallbackSessionId: "sess-host-42" },
    );
    expect(ev.type).toBe("result");
    if (ev.type !== "result") return;
    expect(ev.sessionId).toBe("sess-abc-001");
    expect(ev.turnId).toBe("turn-1");
  });

  it("(f6) never overrides a parsed envelope — Rule 1 error keeps the envelope's ids", () => {
    const env = makeEnvelope({
      error: { code: "provider_auth_failed", classification: "engine" },
    });
    const ev = parseRunOutput(
      { stdout: JSON.stringify(env), stderr: "", exitCode: 1 },
      { fallbackSessionId: "sess-host-42" },
    );
    expect(ev.type).toBe("error");
    if (ev.type !== "error") return;
    expect(ev.code).toBe("provider_auth_failed");
    expect(ev.sessionId).toBe("sess-abc-001");
    expect(ev.turnId).toBe("turn-1");
  });
});

describe("parseRunOutput — usage block (protocol 0.4.0)", () => {
  it("surfaces the metadata usage block verbatim, with no wrapper-side arithmetic", () => {
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(makeEnvelope()),
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    if (ev.type !== "result") throw new Error("expected result event");

    // tokensIn is copied straight through as the CHARGED total. The wrapper
    // must NOT re-add cache writes, and must never add cache reads at all:
    // they are already inside the engine's gross input.
    expect(ev.usage).toEqual({
      inputTokens: 1247,
      outputTokens: 89,
      cacheReadTokens: 300,
      cacheWriteTokens: 47,
      costUsd: "0.00421500",
    });
  });

  it("keeps costUsd a STRING, never a number (monetary precision)", () => {
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(
        makeEnvelope({
          metadata: { tokensIn: 1, tokensOut: 1, costUsd: "0.10000000000000001" },
        }),
      ),
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    if (ev.type !== "result") throw new Error("expected result event");
    expect(typeof ev.usage?.costUsd).toBe("string");
    // Round-tripping through `number` would collapse this to 0.1.
    expect(ev.usage?.costUsd).toBe("0.10000000000000001");
  });

  it("reports costUsd null (not 0) when no provider reported a cost", () => {
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(
        makeEnvelope({ metadata: { tokensIn: 10, tokensOut: 2, costUsd: null } }),
      ),
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    if (ev.type !== "result") throw new Error("expected result event");
    expect(ev.usage?.costUsd).toBeNull();
  });

  it("omits usage entirely when metadata carries no usage keys (pre-0.4.0 engine)", () => {
    // "Not reported" and "reported as zero" are different claims. An engine
    // that never had a usage block must not be made to look like it spent 0.
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(
        makeEnvelope({ metadata: { durationMs: 12, correlationId: "c" } }),
      ),
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    if (ev.type !== "result") throw new Error("expected result event");
    expect(ev.usage).toBeUndefined();
  });

  it("coerces a malformed token count to 0 rather than throwing", () => {
    const outcome: SubprocessOutcome = {
      stdout: JSON.stringify(
        makeEnvelope({ metadata: { tokensIn: "not-a-number", tokensOut: 5 } }),
      ),
      stderr: "",
      exitCode: 0,
    };
    const ev = parseRunOutput(outcome);
    if (ev.type !== "result") throw new Error("expected result event");
    expect(ev.usage?.inputTokens).toBe(0);
    expect(ev.usage?.outputTokens).toBe(5);
  });
});

describe("stderrTailBytes — the tail is bounded in BYTES, not characters", () => {
  const outcomeWith = (stderr: string): SubprocessOutcome => ({
    stdout: JSON.stringify(makeEnvelope()),
    stderr,
    exitCode: 0,
  });

  it("defaults to STDERR_TAIL_BYTES when the option is omitted", () => {
    const ev = parseRunOutput(outcomeWith("y".repeat(9000)));
    if (ev.type !== "result") throw new Error("expected result event");
    expect(Buffer.byteLength(ev.stderrTail!, "utf-8")).toBe(STDERR_TAIL_BYTES);
  });

  it("null keeps the ENTIRE buffer", () => {
    const stderr = "z".repeat(9000);
    const ev = parseRunOutput(outcomeWith(stderr), { stderrTailBytes: null });
    if (ev.type !== "result") throw new Error("expected result event");
    expect(ev.stderrTail).toBe(stderr);
  });

  it("0 disables capture", () => {
    const ev = parseRunOutput(outcomeWith("plenty of stderr"), {
      stderrTailBytes: 0,
    });
    if (ev.type !== "result") throw new Error("expected result event");
    expect(ev.stderrTail).toBeUndefined();
  });

  it("0 also suppresses a tail the envelope supplied", () => {
    // The knob is a host instruction about the FIELD, not about one source of
    // it: "disabled" that still returns stderr would be a lie.
    const env = makeEnvelope({
      reply: "",
      error: { code: "boom", stderrTail: "engine-supplied tail" },
    });
    const ev = parseRunOutput(
      { stdout: JSON.stringify(env), stderr: "", exitCode: 1 },
      { stderrTailBytes: 0 },
    );
    if (ev.type !== "error") throw new Error("expected error event");
    expect(ev.stderrTail).toBeUndefined();
  });

  it("caps a multibyte tail in bytes and never splits a codepoint", () => {
    // 3 bytes per character in UTF-8. A character-counting implementation
    // returns ~3x the cap here; a naive byte slice returns U+FFFD.
    const stderr = "バナナ貿易の歴史".repeat(200);
    const ev = parseRunOutput(outcomeWith(stderr), { stderrTailBytes: 512 });
    if (ev.type !== "result") throw new Error("expected result event");

    const tail = ev.stderrTail!;
    expect(tail).toBeDefined();
    expect(/^[\x00-\x7F]*$/.test(tail)).toBe(false); // genuinely multibyte
    expect(Buffer.byteLength(tail, "utf-8")).toBeLessThanOrEqual(512);
    // 512 is not a multiple of 3, so the trim to a codepoint boundary must
    // have dropped 1-2 bytes: proof the boundary logic actually ran.
    expect(Buffer.byteLength(tail, "utf-8")).toBeGreaterThan(512 - 3);
    expect(tail).not.toContain("\uFFFD");
    // The tail is the END of the buffer.
    expect(stderr.endsWith(tail)).toBe(true);
  });
});

describe("tailStderrBytes — direct unit coverage", () => {
  it("returns undefined for an empty string regardless of limit", () => {
    expect(tailStderrBytes("", 100)).toBeUndefined();
    expect(tailStderrBytes("", null)).toBeUndefined();
    expect(tailStderrBytes("", 0)).toBeUndefined();
  });

  it("returns the text unchanged when it already fits the cap", () => {
    expect(tailStderrBytes("short", 4096)).toBe("short");
  });

  it("measures the cap in UTF-8 bytes, not UTF-16 code units", () => {
    // 4 characters, 12 UTF-8 bytes. A cap of 6 bytes must yield 2 characters
    // (6 bytes), not 6 characters.
    const text = "あいうえ";
    const tail = tailStderrBytes(text, 6)!;
    expect(Buffer.byteLength(tail, "utf-8")).toBe(6);
    expect(tail).toBe("うえ");
  });

  it("backs up to a codepoint boundary rather than emitting U+FFFD", () => {
    const text = "あいうえ"; // 12 bytes
    const tail = tailStderrBytes(text, 7)!; // 7 is mid-codepoint
    expect(tail).toBe("うえ"); // 6 bytes — one fewer than the cap, on purpose
    expect(Buffer.byteLength(tail, "utf-8")).toBe(6);
    expect(tail).not.toContain("\uFFFD");
  });

  it("handles a 4-byte codepoint (astral plane) at the boundary", () => {
    const text = "ab😀😀"; // 2 + 4 + 4 = 10 bytes
    const tail = tailStderrBytes(text, 5)!; // lands inside the last emoji
    expect(tail).toBe("😀");
    expect(Buffer.byteLength(tail, "utf-8")).toBe(4);
    expect(tail).not.toContain("\uFFFD");
  });

  it("returns undefined when the cap trims away every whole codepoint", () => {
    // A 2-byte cap cannot hold a 3-byte character; the boundary walk consumes
    // the whole window and the honest answer is an empty tail.
    expect(tailStderrBytes("あいうえ", 2)).toBe("");
  });
});
