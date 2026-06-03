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
export function assembleArgv(input) {
    const argv = [];
    argv.push("run");
    argv.push("--session-id", input.sessionId);
    argv.push(input.resume ? "--resume" : "--fresh");
    if (input.cwd !== undefined) {
        argv.push("--cwd", input.cwd);
    }
    if (input.providerOverride !== undefined) {
        argv.push("--provider", input.providerOverride);
    }
    argv.push("--output", "json");
    argv.push("--protocol-version", input.protocolVersion);
    // SC-C: wrapper enforces auto-allow at the bundle layer.
    argv.push("-y");
    // Prompt is the final positional argument.
    argv.push(input.prompt);
    return argv;
}
