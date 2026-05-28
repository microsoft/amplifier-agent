/**
 * mcp-spill.ts — MCP servers config path resolution (CR-A, protocol 0.2.0).
 *
 * The wrapper always spills the MCP server map to a 0600 tmpfile under
 * `${XDG_RUNTIME_DIR || os.tmpdir()}/amplifier-agent/<sessionId>/mcp.json`.
 * The file is written in the format documented by amplifier-module-tool-mcp:
 * a top-level `{"mcpServers": <map>}` object. The engine receives the plain
 * file path via `--mcp-config-path` and sets `AMPLIFIER_MCP_CONFIG`; the
 * module reads it via its standard config discovery (config.py priority chain).
 *
 * `cleanupSpillFile` is the matching teardown — idempotent unlink that
 * swallows ENOENT so callers can call it unconditionally on every exit path.
 */
import { mkdir, writeFile, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Result of resolving the `--mcp-config-path` flag value.
 *
 * - When `mcpServers` is null/undefined/empty: `configPath` is `null`.
 * - When servers are present: `configPath` points at the 0600 spill file.
 *   The file contains `{"mcpServers": <map>}` in the format that
 *   amplifier-module-tool-mcp expects when reading `AMPLIFIER_MCP_CONFIG`.
 */
export interface McpSpillResult {
  configPath: string | null;
}

/**
 * Loose shape for an MCP server entry. The full map is written verbatim
 * into the spill file; no field is inspected beyond presence of the key.
 */
interface McpServerLike {
  [k: string]: unknown;
}

type McpServersMap = Record<string, McpServerLike>;

/**
 * Compute the base directory for spill files. Prefers
 * `$XDG_RUNTIME_DIR/amplifier-agent` (typically a tmpfs on Linux) and falls
 * back to `os.tmpdir()/amplifier-agent` otherwise.
 */
function spillBaseDir(): string {
  const xdg = process.env["XDG_RUNTIME_DIR"];
  if (xdg && xdg.length > 0) {
    return join(xdg, "amplifier-agent");
  }
  return join(tmpdir(), "amplifier-agent");
}

/**
 * Resolve the MCP config file path to pass as `--mcp-config-path`.
 *
 * Always spills to a 0600 tmpfile. The file content wraps the server map
 * in the top-level `mcpServers` key that amplifier-module-tool-mcp expects.
 *
 * @param mcpServers Map of server-id -> config, or null/undefined.
 * @param sessionId  Session identifier; used as the per-session subdirectory
 *                   under the spill base so concurrent sessions never clash.
 *
 * @returns A `McpSpillResult` with the on-disk config path (or null if there
 *          are no servers to spill).
 */
export async function resolveMcpConfigPath(
  mcpServers: McpServersMap | null | undefined,
  sessionId: string,
): Promise<McpSpillResult> {
  if (!mcpServers || Object.keys(mcpServers).length === 0) {
    return { configPath: null };
  }

  // Always spill to a 0600 tmpfile under a 0700 per-session dir.
  // Wrap the server map in the top-level "mcpServers" key that the module
  // expects when reading AMPLIFIER_MCP_CONFIG (see tool-mcp/config.py).
  const dir = join(spillBaseDir(), sessionId);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const filePath = join(dir, "mcp.json");
  await writeFile(filePath, JSON.stringify({ mcpServers: mcpServers }), {
    mode: 0o600,
  });

  return { configPath: filePath };
}

/**
 * Idempotently remove a spill file. Safe to call with `null` (no-op) and
 * safe to call when the file is already gone (ENOENT swallowed). Other
 * I/O errors propagate.
 */
export async function cleanupSpillFile(
  configPath: string | null | undefined,
): Promise<void> {
  if (!configPath) return;
  try {
    await unlink(configPath);
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return;
    throw err;
  }
}
