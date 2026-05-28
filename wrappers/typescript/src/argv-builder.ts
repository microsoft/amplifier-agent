/**
 * argv-builder.ts — pure argv assembly for `amplifier-agent run`.
 *
 * Mode A v2 (task-5 / A3'): given a fully-resolved AssembleArgvInput, produce
 * the exact argv array the wrapper will pass to the engine binary. This
 * function performs no I/O and reads no environment — all spilling, env
 * resolution, and capability composition happen upstream.
 *
 * SC-C: the wrapper always passes `-y` to enforce auto-allow at the bundle
 * layer; approvals are handled by the orchestrating host, not the engine.
 */

export interface AssembleArgvInput {
  /** Session identifier (provided by caller, never generated here). */
  sessionId: string;
  /** Final user prompt — emitted last as a positional argument. */
  prompt: string;
  /** Protocol version the wrapper speaks (e.g. "0.1.0"). */
  protocolVersion: string;
  /** When true, emit `--resume` instead of `--fresh`. */
  resume?: boolean;
  /** Working directory override; emits `--cwd <cwd>`. */
  cwd?: string;
  /** Provider override; emits `--provider <providerOverride>`. */
  providerOverride?: string;
  /**
   * Path to the MCP config JSON file, pre-spilled by `resolveMcpConfigPath`.
   * Passed to the engine as `--mcp-config-path <path>`; the engine sets
   * `AMPLIFIER_MCP_CONFIG` so the tool-mcp module loads it during mount.
   */
  mcpConfigPath?: string;
  /** Host capabilities object — emitted as `--host-capabilities <JSON>`. */
  hostCapabilities?: unknown;
  /** Allowlisted env variable names — emits `--env-allowlist <comma-joined>`. */
  envAllowlist?: string[];
  /** Extra env entries — emitted as `--env-extra <JSON>`. */
  envExtra?: Record<string, string>;
  /** When true, emit `--allow-protocol-skew`. */
  allowProtocolSkew?: boolean;
}

/**
 * Build the argv array for `amplifier-agent run`.
 *
 * Pure function: no I/O, no env reads, no globals. Order is canonical and
 * stable so wrapper integration tests can pin against it.
 */
export function assembleArgv(input: AssembleArgvInput): string[] {
  const argv: string[] = [];

  argv.push("run");
  argv.push("--session-id", input.sessionId);
  argv.push(input.resume ? "--resume" : "--fresh");

  if (input.cwd !== undefined) {
    argv.push("--cwd", input.cwd);
  }
  if (input.providerOverride !== undefined) {
    argv.push("--provider", input.providerOverride);
  }
  if (input.mcpConfigPath !== undefined) {
    argv.push("--mcp-config-path", input.mcpConfigPath);
  }
  if (input.hostCapabilities !== undefined) {
    argv.push("--host-capabilities", JSON.stringify(input.hostCapabilities));
  }
  if (input.envAllowlist !== undefined && input.envAllowlist.length > 0) {
    argv.push("--env-allowlist", input.envAllowlist.join(","));
  }
  if (input.envExtra !== undefined) {
    argv.push("--env-extra", JSON.stringify(input.envExtra));
  }

  argv.push("--output", "json");
  argv.push("--protocol-version", input.protocolVersion);

  if (input.allowProtocolSkew === true) {
    argv.push("--allow-protocol-skew");
  }

  // SC-C: wrapper enforces auto-allow at the bundle layer.
  argv.push("-y");

  // Prompt is the final positional argument.
  argv.push(input.prompt);

  return argv;
}
