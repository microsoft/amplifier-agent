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
 *      OR when the configured `timeoutMs` elapses (synthesized `engine_hung`).
 *
 * Lifecycle:
 *   - `submit()` is one-shot per session (D10). A second call throws
 *     `AaaError(lifecycle_unsupported)`.
 *   - `cancel()` SIGTERMs the whole process group (SC-B), waits up to 5s, then
 *     SIGKILLs if the engine has not exited. It also unlinks any MCP spill
 *     file created on this turn (CR-A cleanup).
 *   - `dispose()` is a synonym for `cancel()`.
 */
import type { ChildProcess } from "node:child_process";
import type { McpServerConfig } from "./types.js";
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
    /** Protocol version the wrapper speaks (e.g. "0.2.0"). */
    protocolVersion: string;
    /** Per-submit timeout in milliseconds. Defaults to 10 minutes. */
    timeoutMs?: number;
}
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
     *   (vii) race `exitPromise` vs `timeoutPromise`;
     *         on timeout: cancel(), synthesize `engine_hung`;
     *         on exit:    parseRunOutput({stdout, stderr, exitCode});
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
