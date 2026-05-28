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
export declare function resolveMcpConfigPath(mcpServers: McpServersMap | null | undefined, sessionId: string): Promise<McpSpillResult>;
/**
 * Idempotently remove a spill file. Safe to call with `null` (no-op) and
 * safe to call when the file is already gone (ENOENT swallowed). Other
 * I/O errors propagate.
 */
export declare function cleanupSpillFile(configPath: string | null | undefined): Promise<void>;
export {};
