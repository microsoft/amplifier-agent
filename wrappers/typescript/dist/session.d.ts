/**
 * SessionHandle — subprocess driver for Mode A v2 (A3').
 *
 * Per the 2026-05-24 Mode A pivot amendment §5.2: each `submit()` call spawns a
 * fresh `amplifier-agent run` subprocess with the assembled argv. The async
 * iterable yields:
 *
 *   1. `{type:'init', sessionId}` — yielded SYNCHRONOUSLY before the
 *      subprocess is spawned (SC-1: no race window with the activity ticker).
 *   2. `{type:'activity'}` — yielded every 2 seconds while the subprocess is
 *      alive (preserves NC's stuck-detection signal without engine-side
 *      cooperation).
 *   3. `{type:'result', text}` or `{type:'error', ...}` — yielded once when
 *      the subprocess exits (`parseRunOutput` applied to stdout/stderr/exit)
 *      OR when the configured `timeoutMs` (if a positive number) elapses
 *      (synthesized `engine_hung`). No timeout is armed when `timeoutMs` is
 *      `undefined` or `<= 0`.
 *
 * Lifecycle:
 *   - `submit()` is one-shot per session (D10). A second call throws
 *     `AaaError(lifecycle_unsupported)`.
 *   - `cancel()` SIGTERMs the whole process group (SC-B), waits up to 5s, then
 *     SIGKILLs if the engine has not exited. It also unlinks any MCP spill
 *     file created on this turn (CR-A cleanup).
 *   - `dispose()` is a synonym for `cancel()`.
 */
import type { ChildProcess, SpawnOptions } from "node:child_process";
import type { McpServerConfig } from "./types.js";
/**
 * Factory function compatible with the surface of `child_process.spawn`
 * that `SessionHandle` calls. Hosts can supply this via
 * `SpawnAgentParams.runChildProcess` (Issue #3) to substitute their own
 * subprocess factory — useful for sandboxing, harness wrapping, or test
 * doubles.
 *
 * The factory is invoked exactly once per `submit()` with the resolved
 * binary path, the assembled argv array, and the spawn options the wrapper
 * would have used (including `detached`, `stdio`, `env`, and optional `cwd`).
 *
 * @public
 */
export type ChildProcessFactory = (command: string, args: readonly string[], options: SpawnOptions) => ChildProcess;
/**
 * A display event yielded by `SessionHandle.submit()`.
 *
 * Mode A v2 (CR-C, amendment §5.2): a discriminated union narrow enough that
 * every variant's payload is exhaustively typed. The fields removed from the
 * pre-amendment shape (`turnId`, `parentTurnId`, `synthesized`, `payload`)
 * cannot be meaningfully populated on the Mode A wire.
 */
export type DisplayEvent = {
    type: "init";
    sessionId: string;
} | {
    type: "activity";
} | {
    type: "result";
    text: string;
} | {
    type: "error";
    code: string;
    classification: "transport" | "protocol" | "engine" | "approval" | "unknown";
    severity: "error" | "warning";
    correlationId: string;
    message: string;
    stderrTail?: string;
    retryable: boolean;
}
/**
 * Wire-protocol notification dispatched from the engine's stderr NDJSON
 * stream (Issue #2 / #6). One of the 9 wire event types the engine
 * emits: progress, result/delta, result/final, thinking/delta,
 * thinking/final, tool/started, tool/completed, approval/request,
 * approval/timeout, plus the wire-level error notification.
 *
 * `method` is the JSON-RPC method name verbatim from the wire envelope
 * (e.g. `"progress"`, `"tool/started"`). `params` is the raw payload
 * dictionary the engine emitted, unaltered. Callers may narrow on
 * `method` and cast `params` to a typed shape from `./types.ts`.
 */
 | {
    type: "notification";
    method: string;
    params: unknown;
};
/** Typed error for AaA wrapper lifecycle and protocol violations. */
export declare class AaaError extends Error {
    code: string;
    remediation?: string;
    classification?: "transport" | "protocol" | "engine" | "approval" | "unknown";
    severity?: "error" | "warning";
    correlationId?: string;
    stderrTail?: string;
    constructor(code: string, remediation?: string, opts?: {
        classification?: AaaError["classification"];
        severity?: AaaError["severity"];
        correlationId?: string;
        stderrTail?: string;
    });
}
/** Info returned by SessionHandle.getEngineInfo() (D5). */
export interface EngineInfo {
    binaryPath: string;
    protocolVersion: string;
    engineVersion: string;
    bundleDigest: string;
}
/**
 * Parameters for constructing a `SessionHandle` (amendment §5.2).
 *
 * All session config is captured up-front and stored as instance state. The
 * subprocess is not spawned until `submit()` is called.
 */
export interface SessionHandleParams {
    /** Resolved absolute path to the amplifier-agent binary. */
    binaryPath: string;
    /** Session identifier (caller-supplied or minted by spawnAgent). */
    sessionId: string;
    /** Subprocess environment (already filtered by `buildEnv`). */
    subprocessEnv: Record<string, string>;
    /** When true, emit `--resume`; otherwise emit `--fresh`. */
    resume?: boolean;
    /** Working directory for the subprocess. */
    cwd?: string;
    /**
     * MCP servers to forward to the engine. CR-A spill applies: the map is
     * written to a 0600 tmpfile and the path is injected into the engine's
     * subprocess environment as `AMPLIFIER_MCP_CONFIG`. The former
     * `--mcp-config-path` argv flag was removed.
     */
    mcpServers?: Record<string, McpServerConfig>;
    /** Provider override forwarded via `--provider`. */
    providerOverride?: string;
    /**
     * Path to the engine's host config file (Issue #1). Forwarded to the
     * engine via `--config <configPath>`. See the engine's
     * `single_turn` mode for the resolved-precedence rules between argv
     * flags, host_config, and the bundle's TTY-based default.
     */
    configPath?: string;
    /**
     * Approval mode override (Issue #10). Maps to engine argv:
     * `"yes" -> -y`, `"no" -> -n`, `"prompt" -> emit nothing` so the engine
     * falls back to `host_config.approval.mode` or its bundle/TTY default.
     * Undefined preserves the historical default (`-y`) for callers that
     * have not opted into the approval API.
     */
    approvalMode?: "yes" | "no" | "prompt";
    /**
     * Stderr display mode forwarded to the engine via `--display <mode>`.
     *
     * - `"ndjson"` — required for hosts that consume structured wire events
     *   via the `display.onEvent` callback below. The engine emits one
     *   JSON-RPC notification per line on stderr, matching the
     *   `parseNdjsonStream` consumer this wrapper wires onto `child.stderr`.
     *   This is the only way to receive enriched `usage` fields (cost,
     *   model, provider, cache token counts, llm duration, etc.) the
     *   streaming hook produces.
     * - `"text"` — engine emits human-readable text via CliDisplaySystem.
     *   The wrapper's NDJSON consumer cannot decode it, so `display.onEvent`
     *   stays silent. Useful only for direct CLI use, not wrapper consumers.
     * - omitted — wrapper emits no `--display` flag. Engine defaults to
     *   `text`, preserving the historical pre-#45 behavior. Use this for
     *   compatibility with older engines that don't accept `--display`.
     *
     * Requires engine support for the `--display` flag. Older engines
     * (pre-#45-followup) fail with `click` "no such option" if this is set.
     */
    displayMode?: "text" | "ndjson";
    /**
     * Workspace name for isolating session state by project. Forwarded to the
     * engine via `--workspace <name>`. When unset, the engine auto-derives a
     * slug from the cwd basename + 8-char sha256 of the resolved cwd path.
     *
     * Hosts that manage multiple agents per process should set this so each
     * agent's transcripts land in a separate directory under
     * `~/.local/state/amplifier-agent/workspaces/<workspace>/sessions/<id>/`.
     *
     * Must satisfy `[a-z0-9][a-z0-9-]{0,63}`. The engine validates and rejects
     * invalid slugs with `argv_workspace_invalid`.
     */
    workspace?: string;
    /** Protocol version the wrapper speaks (e.g. "0.3.0"). */
    protocolVersion: string;
    /**
     * Per-submit timeout in milliseconds. No timeout is applied unless a
     * positive value is provided. `undefined` or `<= 0` disables the
     * wall-clock hang timer entirely. Pass `DEFAULT_TIMEOUT_MS` to opt into
     * the original 10-minute cap.
     */
    timeoutMs?: number;
    /**
     * Optional override for the subprocess factory (Issue #3). When set, the
     * handle invokes this function instead of `child_process.spawn`.
     */
    runChildProcess?: ChildProcessFactory;
    /**
     * Display sink (Issue #4). When set, every parsed NDJSON wire-event from
     * the engine subprocess's stderr stream is dispatched here, in addition
     * to whatever the iterator yields. Pass-through from
     * `SpawnAgentParams.display`.
     */
    display?: {
        onEvent?: (event: DisplayEvent) => void;
    };
    /** Engine metadata resolved at spawnAgent() time (Issue #7). */
    engineVersion?: string;
    /** Bundle digest resolved at spawnAgent() time (Issue #7). */
    bundleDigest?: string;
}
/**
 * Default timeout value (10 minutes) exported for callers that want a
 * wall-clock cap on individual turns. This constant is NOT applied
 * automatically — pass it explicitly as `timeoutMs: DEFAULT_TIMEOUT_MS`
 * to opt into the 10-minute limit.
 *
 * @public
 */
export declare const DEFAULT_TIMEOUT_MS: number;
/**
 * Wait for `child` to fire `exit`, or `ms` to elapse — whichever first.
 *
 * Resolves either way (the caller inspects `child.exitCode` / `signalCode` to
 * determine which path was taken). Idempotent: if the child has already
 * exited, resolves immediately.
 */
export declare function waitForExitOrTimeout(child: ChildProcess, ms: number): Promise<void>;
/** One-shot session handle that drives the engine subprocess. */
export declare class SessionHandle {
    private readonly params;
    private submitted;
    private subprocess;
    private mcpSpillPath;
    private readonly engineInfo;
    constructor(params: SessionHandleParams);
    /** Return resolved engine metadata (D5). */
    getEngineInfo(): EngineInfo;
    /**
     * Submit a prompt and return an AsyncIterable of DisplayEvents.
     *
     * One-shot per session (D10): throws AaaError on second call.
     */
    submit(prompt: string): AsyncIterable<DisplayEvent>;
    /**
     * Async generator implementing the §5.2 iterable behavior:
     *   (i)   yield `{type:'init', sessionId}` synchronously (SC-1);
     *   (ii)  CR-A: spill MCP servers to a 0600 tmpfile (when provided);
     *   (iii) build argv via `assembleArgv` and the subprocess env (injecting
     *         `AMPLIFIER_MCP_CONFIG` for the spilled path when present — the
     *         former `--mcp-config-path` argv flag was removed);
     *   (iv)  SC-B: spawn with `detached:true` so PID == PGID for group signals;
     *   (v)   accumulate stdout/stderr from chunks;
     *   (vi)  start a 2s activity ticker → queue;
     *   (vii) race `exitPromise` vs `timeoutPromise` (timer only armed when
     *         `timeoutMs > 0`); on timeout: cancel(), synthesize `engine_hung`;
     *         on exit: parseRunOutput({stdout, stderr, exitCode});
     *   (viii) cleanup spill file after exit;
     *   (ix)  drain queue until the final event is yielded.
     */
    private makeIterable;
    /**
     * Cancel the running subprocess via SIGTERM-then-SIGKILL on the whole
     * process group (SC-B), then unlink any MCP spill file (CR-A).
     *
     * Idempotent: safe to call when the subprocess has already exited and safe
     * to call when no spill file was created. Errors from `process.kill` are
     * swallowed (`ESRCH` means the group is already gone).
     */
    cancel(): Promise<void>;
    /** Graceful shutdown — alias for `cancel()` (D3). */
    dispose(): Promise<void>;
}
