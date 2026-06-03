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
    /** Protocol version the wrapper speaks (e.g. "0.2.0"). */
    protocolVersion: string;
    /** When true, emit `--resume` instead of `--fresh`. */
    resume?: boolean;
    /** Working directory override; emits `--cwd <cwd>`. */
    cwd?: string;
    /** Provider override; emits `--provider <providerOverride>`. */
    providerOverride?: string;
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
 *
 * The former `--mcp-config-path` flag was removed; MCP config is now
 * forwarded via the `AMPLIFIER_MCP_CONFIG` env var injected into the
 * engine's subprocess environment at spawn time (or via
 * `host_config["mcp"]["configPath"]` in the host's config file).
 */
export declare function assembleArgv(input: AssembleArgvInput): string[];
