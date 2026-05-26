/**
 * Tests for mcp-spill.ts: resolveMcpServersFlag() and cleanupSpillFile()
 *
 * TDD cases (task-6 / A3'/CR-A):
 * (i)   null/undefined mcpServers returns { flag: null, spillPath: null }
 * (ii)  no server has non-empty env block -> inline JSON, no spill file
 *       (flag does not start with '@')
 * (iii) any server has non-empty env block -> spill to tmpfile, flag is
 *       `@<path>`, file contains full config, mode is 0600
 * (iv)  cleanupSpillFile is idempotent (ENOENT is fine)
 */
import { describe, it, expect, afterEach } from "vitest";
import { stat, readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  resolveMcpServersFlag,
  cleanupSpillFile,
} from "../src/mcp-spill.js";
import type { McpSpillResult } from "../src/mcp-spill.js";

const SID = "test-session-abc";

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

describe("resolveMcpServersFlag", () => {
  it("(i) returns {null, null} for null mcpServers", async () => {
    const result: McpSpillResult = await resolveMcpServersFlag(null, SID);
    expect(result).toEqual({ flag: null, spillPath: null });
  });

  it("(i) returns {null, null} for undefined mcpServers", async () => {
    const result: McpSpillResult = await resolveMcpServersFlag(undefined, SID);
    expect(result).toEqual({ flag: null, spillPath: null });
  });

  it("(i) returns {null, null} for empty mcpServers object", async () => {
    const result: McpSpillResult = await resolveMcpServersFlag({}, SID);
    expect(result).toEqual({ flag: null, spillPath: null });
  });

  it("(ii) inlines JSON when no server has a non-empty env block", async () => {
    const mcpServers = {
      alpha: { command: "echo", args: ["hi"] },
      // env present but empty -> still considered "no env" for spill purposes
      beta: { command: "true", env: {} },
    };
    const result = await resolveMcpServersFlag(mcpServers, SID);
    expect(result.spillPath).toBeNull();
    expect(result.flag).not.toBeNull();
    // Inline JSON: must NOT start with '@'
    expect(result.flag!.startsWith("@")).toBe(false);
    expect(JSON.parse(result.flag!)).toEqual(mcpServers);
  });

  it("(iii) spills to tmpfile with 0600 mode when any server has a non-empty env block", async () => {
    const mcpServers = {
      alpha: { command: "echo" },
      secret: {
        command: "run-secret",
        env: { API_KEY: "super-secret-value" },
      },
    };
    const result = await resolveMcpServersFlag(mcpServers, SID);
    expect(result.spillPath).not.toBeNull();
    expect(result.flag).not.toBeNull();
    created.push(result.spillPath!);

    // Flag should be '@<path>'
    expect(result.flag).toBe(`@${result.spillPath}`);
    expect(result.flag!.startsWith("@")).toBe(true);

    // File contents should be the full mcpServers config
    const contents = await readFile(result.spillPath!, "utf8");
    expect(JSON.parse(contents)).toEqual(mcpServers);

    // File mode should be 0600 (owner read/write only)
    const st = await stat(result.spillPath!);
    // Mask out file-type bits, keep permission bits only
    const mode = st.mode & 0o777;
    expect(mode).toBe(0o600);
  });

  it("(iv) cleanupSpillFile is idempotent — second call on missing file does not throw", async () => {
    // Create a file we know exists, then cleanup twice.
    const dir = await mkdtemp(join(tmpdir(), "mcp-spill-cleanup-"));
    const path = join(dir, "mcp.json");
    // write a dummy file
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, "{}", { mode: 0o600 });

    // First cleanup removes it
    await expect(cleanupSpillFile(path)).resolves.toBeUndefined();

    // Second cleanup on missing path must not throw (ENOENT swallowed)
    await expect(cleanupSpillFile(path)).resolves.toBeUndefined();

    // remove tmp dir
    await rm(dir, { recursive: true, force: true });
  });

  it("(iv) cleanupSpillFile is a no-op for null input", async () => {
    await expect(cleanupSpillFile(null)).resolves.toBeUndefined();
  });
});
