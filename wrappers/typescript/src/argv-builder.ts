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
  /**
   * Stderr display mode forwarded to the engine via `--display <mode>`.
   *
   * - `"ndjson"` — engine emits one JSON-RPC notification per line on stderr,
   *   matching the `parseNdjsonStream` consumer this wrapper already wires
   *   onto `child.stderr`. Hosts that consume `display.onEvent` typed
   *   notifications (cost, model, cache tokens, llm duration, etc.) MUST set
   *   this; otherwise the engine emits human-readable `[type] summary` text
   *   that `parseNdjsonStream` cannot decode and the notification path
   *   stays silent.
   * - `"text"` — engine emits human-readable text via CliDisplaySystem.
   *   Useful only for direct CLI use; the wrapper's NDJSON consumer can't
   *   decode it.
   * - omitted — wrapper emits no `--display` flag; engine defaults to `text`.
   *   Preserved as the historical default so existing callers who haven't
   *   opted into structured consumption keep their old behavior.
   *
   * Requires engine support for the `--display` flag (added alongside
   * JsonDisplaySystem). Older engines will fail with a click "no such option"
   * error if this is set; coordinate the engine version with the wrapper
   * version (link:/file:/published-pair) before opting in.
   */
  displayMode?: "text" | "ndjson";
  /**
   * Workspace name for isolating session state by project. Forwarded to the
   * engine via `--workspace <name>`.
   *
   * When set, the engine writes session state to
   * `~/.local/state/amplifier-agent/workspaces/<workspace>/sessions/<id>/`.
   * When omitted, the engine auto-derives a slug from the cwd basename plus
   * an 8-char sha256 of the resolved cwd path (e.g. `default-9e80f0e7`).
   *
   * Hosts that manage multiple agents per process (e.g. paperclip's
   * amplifier-local adapter, which runs CEO + CTO + Coder + … per company)
   * should set this so each agent's transcripts land in a separate
   * directory. A typical scheme is `pc-<company-id-short>-<agent-id-short>`.
   *
   * Must satisfy the engine's slug grammar: `[a-z0-9][a-z0-9-]{0,63}`.
   * The engine validates and rejects invalid slugs with `argv_workspace_invalid`.
   */
  workspace?: string;
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
  // `~/.local/state/amplifier-agent/workspaces/<workspace>/sessions/<id>/`
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
  } else if (mode === "no") {
    argv.push("-n");
  } else {
    // mode === "prompt": deliberately emit no flag.
  }

  // Prompt is the final positional argument.
  argv.push(input.prompt);

  return argv;
}
