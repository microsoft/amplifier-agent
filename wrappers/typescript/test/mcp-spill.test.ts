/**
 * Tests for mcp-spill.ts: resolveMcpConfigPath() and cleanupSpillFile()
 *
 * TDD cases (protocol 0.2.0 — always-spill, wrapped format):
 * (i)   null/undefined/empty mcpServers returns { configPath: null }
 * (ii)  non-empty mcpServers always spills to tmpfile; file contains
 *       { mcpServers: <map> } (top-level wrapper required by tool-mcp);
 *       file mode is 0600; no inline JSON branch.
 * (iii) configPath is a plain path (no '@' prefix)
 * (iv)  cleanupSpillFile is idempotent (ENOENT is fine)
 */
import { describe, it, expect, afterEach } from "vitest";
import { stat, readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  resolveMcpConfigPath,
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

describe("resolveMcpConfigPath", () => {
  it("(i) returns {configPath: null} for null mcpServers", async () => {
    const result: McpSpillResult = await resolveMcpConfigPath(null, SID);
    expect(result).toEqual({ configPath: null });
  });

  it("(i) returns {configPath: null} for undefined mcpServers", async () => {
    const result: McpSpillResult = await resolveMcpConfigPath(undefined, SID);
    expect(result).toEqual({ configPath: null });
  });

  it("(i) returns {configPath: null} for empty mcpServers object", async () => {
    const result: McpSpillResult = await resolveMcpConfigPath({}, SID);
    expect(result).toEqual({ configPath: null });
  });

  it("(ii) always spills to tmpfile, even when no server has an env block", async () => {
    const mcpServers = {
      alpha: { command: "echo", args: ["hi"] },
      beta: { command: "true" },
    };
    const result = await resolveMcpConfigPath(mcpServers, SID);
    expect(result.configPath).not.toBeNull();
    created.push(result.configPath!);

    // configPath is a plain path — no '@' prefix
    expect(result.configPath!.startsWith("@")).toBe(false);

    // File content wraps the server map in top-level "mcpServers" key
    const contents = await readFile(result.configPath!, "utf8");
    expect(JSON.parse(contents)).toEqual({ mcpServers });
  });

  it("(ii) spills with 0600 mode when servers have env blocks", async () => {
    const mcpServers = {
      alpha: { command: "echo" },
      secret: {
        command: "run-secret",
        env: { API_KEY: "super-secret-value" },
      },
    };
    const result = await resolveMcpConfigPath(mcpServers, SID);
    expect(result.configPath).not.toBeNull();
    created.push(result.configPath!);

    // File contents should be wrapped in top-level "mcpServers"
    const contents = await readFile(result.configPath!, "utf8");
    expect(JSON.parse(contents)).toEqual({ mcpServers });

    // File mode should be 0600 (owner read/write only)
    const st = await stat(result.configPath!);
    const mode = st.mode & 0o777;
    expect(mode).toBe(0o600);
  });

  it("(iii) configPath is a plain path under the per-session spill dir", async () => {
    const mcpServers = { srv: { command: "node" } };
    const result = await resolveMcpConfigPath(mcpServers, SID);
    expect(result.configPath).not.toBeNull();
    created.push(result.configPath!);

    // Must not start with '@' (plain path, not an @-prefixed spill reference)
    expect(result.configPath!.startsWith("@")).toBe(false);
    // Must end with mcp.json under the session dir
    expect(result.configPath!).toMatch(/amplifier-agent[/\\]test-session-abc[/\\]mcp\.json$/);
  });

  it("(iv) cleanupSpillFile is idempotent — second call on missing file does not throw", async () => {
    // Create a file we know exists, then cleanup twice.
    const dir = await mkdtemp(join(tmpdir(), "mcp-spill-cleanup-"));
    const path = join(dir, "mcp.json");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, "{}", { mode: 0o600 });

    // First cleanup removes it
    await expect(cleanupSpillFile(path)).resolves.toBeUndefined();

    // Second cleanup on missing path must not throw (ENOENT swallowed)
    await expect(cleanupSpillFile(path)).resolves.toBeUndefined();

    await rm(dir, { recursive: true, force: true });
  });

  it("(iv) cleanupSpillFile is a no-op for null input", async () => {
    await expect(cleanupSpillFile(null)).resolves.toBeUndefined();
  });
});
