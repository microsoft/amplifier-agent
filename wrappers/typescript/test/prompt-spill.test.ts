/**
 * Tests for prompt-spill.ts: resolvePromptFilePath() and cleanupPromptSpillFile()
 *
 * TDD cases:
 * (i)   a prompt under the threshold returns { promptFile: null } and writes
 *       nothing
 * (ii)  a prompt of exactly PROMPT_SPILL_THRESHOLD_BYTES bytes spills (the
 *       comparison is `< threshold`, so the boundary value itself spills)
 * (iii) a prompt one byte below the threshold does not spill
 * (iv)  the threshold is measured in UTF-8 BYTES, not characters: a prompt
 *       with fewer characters than the threshold but more bytes must spill
 * (v)   the spilled file round-trips the prompt verbatim (no newline
 *       translation, no stripping, non-ASCII preserved)
 * (vi)  file mode is 0600
 * (vii) promptFile is <base>/<sessionId>/prompt.txt
 * (viii) cleanupPromptSpillFile removes the file
 * (ix)  cleanupPromptSpillFile is idempotent (ENOENT is fine)
 * (x)   cleanupPromptSpillFile is a no-op for null/undefined
 */
import { describe, it, expect, afterEach } from "vitest";
import { stat, readFile, access, rm } from "node:fs/promises";

import {
  resolvePromptFilePath,
  cleanupPromptSpillFile,
  PROMPT_SPILL_THRESHOLD_BYTES,
} from "../src/prompt-spill.js";
import type { PromptSpillResult } from "../src/prompt-spill.js";

const SID = "test-session-prompt";

// Track every spill file created across tests so afterEach cleans them up
// even when a test fails mid-assertion.
const created: string[] = [];

afterEach(async () => {
  while (created.length > 0) {
    const p = created.pop();
    if (!p) continue;
    try {
      await rm(p, { force: true });
    } catch {
      /* swallow */
    }
  }
});

describe("resolvePromptFilePath", () => {
  it("(i) returns {promptFile: null} for a prompt under the threshold", async () => {
    const prompt = "a short prompt that rides on argv";
    const result: PromptSpillResult = await resolvePromptFilePath(prompt, SID);
    expect(result).toEqual({ promptFile: null });
  });

  it("(ii) spills a prompt of exactly PROMPT_SPILL_THRESHOLD_BYTES bytes", async () => {
    const prompt = "a".repeat(PROMPT_SPILL_THRESHOLD_BYTES);
    expect(Buffer.byteLength(prompt, "utf8")).toBe(PROMPT_SPILL_THRESHOLD_BYTES);

    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    created.push(result.promptFile!);

    const contents = await readFile(result.promptFile!, "utf8");
    expect(contents).toBe(prompt);
  });

  it("(iii) does not spill a prompt one byte below the threshold", async () => {
    const prompt = "a".repeat(PROMPT_SPILL_THRESHOLD_BYTES - 1);
    expect(Buffer.byteLength(prompt, "utf8")).toBe(
      PROMPT_SPILL_THRESHOLD_BYTES - 1,
    );

    const result = await resolvePromptFilePath(prompt, SID);
    expect(result).toEqual({ promptFile: null });
  });

  it("(iv) measures UTF-8 bytes, not characters — spills when characters < threshold but bytes >= threshold", async () => {
    // 6000 CJK characters at 3 bytes each = 18000 bytes. The character count
    // (6000) is far below the threshold; the byte count is above it. A check
    // written against `prompt.length` would wrongly skip the spill here.
    const prompt = "日".repeat(6000);
    expect(prompt.length).toBeLessThan(PROMPT_SPILL_THRESHOLD_BYTES);
    expect(Buffer.byteLength(prompt, "utf8")).toBeGreaterThanOrEqual(
      PROMPT_SPILL_THRESHOLD_BYTES,
    );

    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    created.push(result.promptFile!);

    const contents = await readFile(result.promptFile!, "utf8");
    expect(contents).toBe(prompt);
  });

  it("(v) round-trips the prompt verbatim — leading '---', embedded and trailing newlines, non-ASCII", async () => {
    const prompt =
      "---\ntitle: spill round trip\n---\n" +
      "naïve café — 日本語のテキスト\nsecond line\n".repeat(600);
    expect(prompt.startsWith("---")).toBe(true);
    expect(prompt.endsWith("\n")).toBe(true);
    expect(Buffer.byteLength(prompt, "utf8")).toBeGreaterThanOrEqual(
      PROMPT_SPILL_THRESHOLD_BYTES,
    );

    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    created.push(result.promptFile!);

    // Byte-for-byte identical: no newline translation, no stripping.
    const contents = await readFile(result.promptFile!, "utf8");
    expect(contents).toBe(prompt);
    expect(Buffer.byteLength(contents, "utf8")).toBe(
      Buffer.byteLength(prompt, "utf8"),
    );
  });

  it("(vi) spills with 0600 mode", async () => {
    const prompt = "s".repeat(PROMPT_SPILL_THRESHOLD_BYTES);
    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    created.push(result.promptFile!);

    // File mode should be 0600 (owner read/write only)
    const st = await stat(result.promptFile!);
    const mode = st.mode & 0o777;
    expect(mode).toBe(0o600);
  });

  it("(vii) promptFile is prompt.txt under the per-session spill dir", async () => {
    const prompt = "p".repeat(PROMPT_SPILL_THRESHOLD_BYTES);
    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    created.push(result.promptFile!);

    expect(result.promptFile!).toMatch(
      /amplifier-agent[/\\]test-session-prompt[/\\]prompt\.txt$/,
    );
  });

  it("(viii) cleanupPromptSpillFile removes the spilled file", async () => {
    const prompt = "c".repeat(PROMPT_SPILL_THRESHOLD_BYTES);
    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    const path = result.promptFile!;

    // File exists before cleanup
    await expect(access(path)).resolves.toBeUndefined();

    await expect(cleanupPromptSpillFile(path)).resolves.toBeUndefined();

    // File is gone after cleanup
    await expect(access(path)).rejects.toThrow();
  });

  it("(ix) cleanupPromptSpillFile is idempotent — second call on missing file does not throw", async () => {
    const prompt = "i".repeat(PROMPT_SPILL_THRESHOLD_BYTES);
    const result = await resolvePromptFilePath(prompt, SID);
    expect(result.promptFile).not.toBeNull();
    const path = result.promptFile!;

    // First cleanup removes it
    await expect(cleanupPromptSpillFile(path)).resolves.toBeUndefined();

    // Second cleanup on missing path must not throw (ENOENT swallowed)
    await expect(cleanupPromptSpillFile(path)).resolves.toBeUndefined();
  });

  it("(x) cleanupPromptSpillFile is a no-op for null and undefined input", async () => {
    await expect(cleanupPromptSpillFile(null)).resolves.toBeUndefined();
    await expect(cleanupPromptSpillFile(undefined)).resolves.toBeUndefined();
  });
});
