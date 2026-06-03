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
    /** Protocol version the wrapper speaks (e.g. "0.3.0"). */
    protocolVersion: string;
    /** When true, emit `--resume` instead of `--fresh`. */
    resume?: boolean;
    /** Working directory override; emits `--cwd <cwd>`. */
    cwd?: string;
    /** Provider override; emits `--provider <providerOverride>`. */
    providerOverride?: string;
    /**
     * Path to the engine's host config file (Issue #1). Emits
     * `--config <configPath>`. The engine's `single_turn` mode reads this
     * to compose the host_config layer (approval mode, MCP servers,
     * provider defaults, allowProtocolSkew, etc.) — see
     * `src/amplifier_agent_cli/modes/single_turn.py` (`--config` option).
     */
    configPath?: string;
    /**
     * Approval-mode override forwarded to the engine (Issue #10). When set,
     * emits `-y` (always allow) or `-n` (always deny). `"prompt"` is left
     * implicit so the engine falls back to its host_config approval.mode
     * or its TTY-based default.
     *
     * The wrapper unconditionally emits `-y` from older revisions has been
     * removed — the caller now owns this policy decision.
     */
    approvalMode?: "yes" | "no" | "prompt";
}
/**
 * Build the argv array for `amplifier-agent run`.
 *
 * Pure function: no I/O, no env reads, no globals. Order is canonical and
 * stable so wrapper integration tests can pin against it.
 *
 * Removed argv flags (no longer emitted by this wrapper):
 *   - `--mcp-config-path` (engine PR #29): MCP config is now forwarded via the
 *     `AMPLIFIER_MCP_CONFIG` env var injected into the engine's subprocess
 *     environment at spawn time (or via `host_config["mcp"]["configPath"]` in
 *     the host's config file).
 *   - `--env-allowlist`, `--env-extra` (engine PR #27): env composition is
 *     the host's responsibility. Hosts either set `$AMPLIFIER_AGENT_CONFIG`
 *     in the subprocess env or pass `--config <path>` per turn.
 *   - `--allow-protocol-skew` (engine PR #27): the unsafe override moved to
 *     `host_config.allowProtocolSkew: true` in the JSON config file.
 */
export declare function assembleArgv(input: AssembleArgvInput): string[];
