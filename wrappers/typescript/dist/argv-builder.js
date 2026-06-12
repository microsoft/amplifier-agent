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
 *   - `--provider`, `--model`, `--effort` (engine PR #49): all provider
 *     configuration knobs now flow through `host_config.provider.{module,config}`.
 *     The TS wrapper passes `configPath`; the engine reads everything from there.
 */
export function assembleArgv(input) {
    const argv = [];
    argv.push("run");
    argv.push("--session-id", input.sessionId);
    argv.push(input.resume ? "--resume" : "--fresh");
    if (input.cwd !== undefined) {
        argv.push("--cwd", input.cwd);
    }
    // Issue #1: surface the engine's --config flag.
    if (input.configPath !== undefined) {
        argv.push("--config", input.configPath);
    }
    argv.push("--output", "json");
    argv.push("--protocol-version", input.protocolVersion);
    // Optional --display flag. Only emit when explicitly set so older engines
    // (which don't accept --display) keep working with this wrapper. New hosts
    // that consume the structured wire-event stream (paperclip's amplifier-local
    // adapter, future SDK consumers) should set this to "ndjson" so the engine
    // emits one JSON-RPC notification per line on stderr -- the shape
    // `parseNdjsonStream` in `session.ts` expects.
    if (input.displayMode !== undefined) {
        argv.push("--display", input.displayMode);
    }
    // Optional --workspace flag. When set, the engine writes session state to
    // `~/.amplifier-agent/state/workspaces/<workspace>/sessions/<id>/`
    // instead of auto-deriving the slug from cwd. Hosts that manage multiple
    // agents per process should set this so transcripts don't mingle.
    if (input.workspace !== undefined && input.workspace.length > 0) {
        argv.push("--workspace", input.workspace);
    }
    // Issue #10: approval policy is now caller-controlled.
    // - "yes"    -> -y (always allow)
    // - "no"     -> -n (always deny)
    // - "prompt" -> emit nothing; engine falls back to host_config.approval.mode
    //              or the bundle's TTY-based default. This is the only way to
    //              defer to the engine's policy resolution.
    // - undefined -> preserve historical default (-y) so existing callers
    //              who haven't opted into the approval API are unaffected.
    const mode = input.approvalMode;
    if (mode === "yes" || mode === undefined) {
        argv.push("-y");
    }
    else if (mode === "no") {
        argv.push("-n");
    }
    else {
        // mode === "prompt": deliberately emit no flag.
    }
    // Prompt is the final positional argument.
    argv.push(input.prompt);
    return argv;
}
