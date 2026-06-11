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
import { spawn as childSpawn } from "node:child_process";
import { assembleArgv } from "./argv-builder.js";
import { resolveMcpConfigPath, cleanupSpillFile } from "./mcp-spill.js";
import { parseRunOutput, STDERR_TAIL_BYTES } from "./run-output-parser.js";
import { parseNdjsonStream } from "./transport.js";
/** Typed error for AaA wrapper lifecycle and protocol violations. */
export class AaaError extends Error {
    code;
    remediation;
    classification;
    severity;
    correlationId;
    stderrTail;
    constructor(code, remediation, opts) {
        super(remediation ?? code);
        this.code = code;
        this.remediation = remediation;
        this.name = "AaaError";
        if (opts) {
            this.classification = opts.classification;
            this.severity = opts.severity;
            this.correlationId = opts.correlationId;
            this.stderrTail = opts.stderrTail;
        }
    }
}
/**
 * Default timeout value (10 minutes) exported for callers that want a
 * wall-clock cap on individual turns. This constant is NOT applied
 * automatically — pass it explicitly as `timeoutMs: DEFAULT_TIMEOUT_MS`
 * to opt into the 10-minute limit.
 *
 * @public
 */
export const DEFAULT_TIMEOUT_MS = 10 * 60 * 1000;
/** Activity ticker interval: 2 seconds (NC stuck-detection has 10s threshold). */
const ACTIVITY_TICK_MS = 2000;
/** Grace window between SIGTERM and SIGKILL in `cancel()`. */
const SIGKILL_GRACE_MS = 5000;
/**
 * Wait for `child` to fire `exit`, or `ms` to elapse — whichever first.
 *
 * Resolves either way (the caller inspects `child.exitCode` / `signalCode` to
 * determine which path was taken). Idempotent: if the child has already
 * exited, resolves immediately.
 */
export function waitForExitOrTimeout(child, ms) {
    return new Promise((resolve) => {
        if (child.exitCode !== null || child.signalCode !== null) {
            resolve();
            return;
        }
        const timer = setTimeout(() => {
            resolve();
        }, ms);
        child.once("exit", () => {
            clearTimeout(timer);
            resolve();
        });
    });
}
/** Last `STDERR_TAIL_BYTES` chars of `stderr`, or undefined if empty. */
function stderrTailOf(stderr) {
    if (!stderr)
        return undefined;
    if (stderr.length <= STDERR_TAIL_BYTES)
        return stderr;
    return stderr.slice(stderr.length - STDERR_TAIL_BYTES);
}
/** One-shot session handle that drives the engine subprocess. */
export class SessionHandle {
    params;
    submitted = false;
    subprocess = null;
    mcpSpillPath = null;
    engineInfo;
    constructor(params) {
        this.params = params;
        this.engineInfo = {
            binaryPath: params.binaryPath,
            protocolVersion: params.protocolVersion,
            // Issue #7: engineVersion + bundleDigest are populated from the engine
            // version probe that spawnAgent() runs during initialization (Issue #9).
            // Falls back to empty strings when the engine omits a field (e.g. the
            // `version --json` payload does not include bundleDigest until a future
            // engine release ships an admin endpoint exposing it).
            engineVersion: params.engineVersion ?? "",
            bundleDigest: params.bundleDigest ?? "",
        };
    }
    /** Return resolved engine metadata (D5). */
    getEngineInfo() {
        return this.engineInfo;
    }
    /**
     * Submit a prompt and return an AsyncIterable of DisplayEvents.
     *
     * One-shot per session (D10): throws AaaError on second call.
     */
    submit(prompt) {
        if (this.submitted) {
            throw new AaaError("lifecycle_unsupported", "SessionHandle.submit() is one-shot per session (D10); already submitted");
        }
        this.submitted = true;
        return this.makeIterable(prompt);
    }
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
    async *makeIterable(prompt) {
        // (i) SC-1: yield init synchronously, BEFORE any async work.
        yield { type: "init", sessionId: this.params.sessionId };
        // (ii) CR-A: spill MCP servers to a 0600 tmpfile (configPath is null when
        // mcpServers is null/empty).
        // Cast through `unknown`: McpServerConfig is the schema-validated wire type
        // (no index signature), while resolveMcpConfigPath's McpServerLike has an
        // open index signature. The two are runtime-compatible but TS rightly
        // rejects assignability without the cast.
        const spill = await resolveMcpConfigPath((this.params.mcpServers ?? null), this.params.sessionId);
        this.mcpSpillPath = spill.configPath;
        // (iii) build argv (pure function — no I/O). The MCP config path is
        // forwarded to the engine via AMPLIFIER_MCP_CONFIG (subprocess env)
        // rather than via an argv flag. `configPath` (Issue #1) and
        // `approvalMode` (Issue #10) are forwarded via argv flags emitted
        // by assembleArgv.
        const argv = assembleArgv({
            sessionId: this.params.sessionId,
            prompt,
            protocolVersion: this.params.protocolVersion,
            resume: this.params.resume,
            cwd: this.params.cwd,
            providerOverride: this.params.providerOverride,
            configPath: this.params.configPath,
            approvalMode: this.params.approvalMode,
            displayMode: this.params.displayMode,
            workspace: this.params.workspace,
        });
        // Build the subprocess env. When we spilled an MCP config, set
        // AMPLIFIER_MCP_CONFIG so tool-mcp reads it natively via its
        // config-discovery priority chain. We copy the params env into a fresh
        // object so the stored SessionHandle env is never mutated.
        const subprocessEnv = {
            ...this.params.subprocessEnv,
        };
        if (spill.configPath !== null) {
            subprocessEnv.AMPLIFIER_MCP_CONFIG = spill.configPath;
        }
        // (iv) SC-B: spawn detached → new session group → PID == PGID.
        // Issue #3: when the caller provided a `runChildProcess` factory, use it
        // in place of `child_process.spawn`. The factory must satisfy the
        // ChildProcessFactory contract (same signature surface SessionHandle
        // requires).
        const spawnFn = this.params.runChildProcess ?? childSpawn;
        const child = spawnFn(this.params.binaryPath, argv, {
            detached: true,
            stdio: ["ignore", "pipe", "pipe"],
            env: subprocessEnv,
            ...(this.params.cwd !== undefined ? { cwd: this.params.cwd } : {}),
        });
        this.subprocess = child;
        // (v) accumulate stdout/stderr from chunks.
        let stdoutBuf = "";
        let stderrBuf = "";
        child.stdout?.on("data", (chunk) => {
            stdoutBuf += typeof chunk === "string" ? chunk : chunk.toString("utf-8");
        });
        // Issue #2 / #6: wire `parseNdjsonStream` onto the child's stderr. The
        // engine emits one JSON object per line for each wire-protocol
        // notification (progress, result/delta, tool/started, etc.).
        // - JSON lines are parsed into `notification` DisplayEvents and
        //   delivered via TWO paths so hosts can choose either consumption
        //   model:
        //     1. Pushed onto the iterator queue so `for await (const ev of
        //        handle.submit(...))` yields them. Iterator consumers (e.g.
        //        paperclip's amplifier-local adapter, which switches on
        //        `event.type === "notification"`) need this path.
        //     2. Dispatched to `params.display?.onEvent` (Issue #4) for hosts
        //        that prefer a push-based callback.
        //   Hosts that subscribe via BOTH paths will receive each
        //   notification twice -- acceptable; subscribe to one or the other.
        // - Non-JSON lines are accumulated into `stderrBuf` so the
        //   stderrTail surface on parseRunOutput stays useful for
        //   diagnostic snapshots.
        // - JSON lines are also appended to `stderrBuf` verbatim, so a
        //   crash-time tail still contains the wire-event context.
        const displayOnEvent = this.params.display?.onEvent;
        if (child.stderr) {
            void parseNdjsonStream(child.stderr, {
                onJson: (obj) => {
                    stderrBuf += JSON.stringify(obj) + "\n";
                    const method = typeof obj.method === "string" ? obj.method : "unknown";
                    const params = "params" in obj ? obj.params : obj;
                    const ev = { type: "notification", method, params };
                    // Path 1: iterator queue.
                    push(ev);
                    // Path 2: callback (legacy / push-only hosts).
                    if (displayOnEvent) {
                        displayOnEvent(ev);
                    }
                },
                onNonJson: (line) => {
                    stderrBuf += line + "\n";
                },
            });
        }
        const queue = [];
        let wake = null;
        const push = (item) => {
            queue.push(item);
            if (wake !== null) {
                const w = wake;
                wake = null;
                w();
            }
        };
        // Single-shot finalize: whichever of {timeout, exit, spawn error} wins
        // pushes the terminal event + done sentinel; later events are ignored.
        let finalized = false;
        const finalize = (ev) => {
            if (finalized)
                return;
            finalized = true;
            push(ev);
            push({ _done: true });
        };
        // (vi) 2s activity ticker.
        const ticker = setInterval(() => {
            if (!finalized)
                push({ type: "activity" });
        }, ACTIVITY_TICK_MS);
        // (vii) race exit vs timeout (timer only armed when timeoutMs is a positive
        // number; undefined/0/negative means no timeout is applied).
        const timeoutMs = this.params.timeoutMs;
        let timeoutHandle = null;
        if (timeoutMs !== undefined && timeoutMs > 0) {
            timeoutHandle = setTimeout(() => {
                // Synthesize engine_hung before invoking cancel(), so the iterator
                // yields a terminal error even if SIGTERM/SIGKILL hangs.
                const tail = stderrTailOf(stderrBuf);
                finalize({
                    type: "error",
                    code: "engine_hung",
                    classification: "engine",
                    severity: "error",
                    correlationId: "",
                    message: `Engine subprocess hung past ${timeoutMs}ms timeout; SIGTERM/SIGKILL escalation invoked.`,
                    ...(tail !== undefined ? { stderrTail: tail } : {}),
                    retryable: false,
                });
                // Fire-and-forget: cancel races the next event-loop turn.
                void this.cancel();
            }, timeoutMs);
        }
        child.once("exit", (code, _signal) => {
            if (timeoutHandle !== null)
                clearTimeout(timeoutHandle);
            if (finalized)
                return;
            const ev = parseRunOutput({
                stdout: stdoutBuf,
                stderr: stderrBuf,
                exitCode: code ?? -1,
            });
            finalize(ev);
        });
        // Spawn failures (ENOENT, EACCES) emit 'error' before 'exit'. Treat them
        // as transport-class failures; the test suite covers spawn-rejection at
        // this seam (binary missing → typed AaaError-shaped DisplayEvent).
        child.once("error", (err) => {
            if (timeoutHandle !== null)
                clearTimeout(timeoutHandle);
            if (finalized)
                return;
            const tail = stderrTailOf(stderrBuf);
            finalize({
                type: "error",
                code: "spawn_failed",
                classification: "transport",
                severity: "error",
                correlationId: "",
                message: `Failed to spawn engine subprocess (${err.code ?? "unknown"}): ${err.message}`,
                ...(tail !== undefined ? { stderrTail: tail } : {}),
                retryable: false,
            });
        });
        // (ix) drain loop — yield activity events then the final event.
        try {
            // eslint-disable-next-line no-constant-condition
            while (true) {
                while (queue.length > 0) {
                    const item = queue.shift();
                    if ("_done" in item) {
                        return;
                    }
                    yield item;
                }
                await new Promise((resolve) => {
                    wake = resolve;
                });
            }
        }
        finally {
            // (viii) cleanup ticker + spill file on every exit path.
            clearInterval(ticker);
            if (timeoutHandle !== null)
                clearTimeout(timeoutHandle);
            await cleanupSpillFile(this.mcpSpillPath);
            this.mcpSpillPath = null;
        }
    }
    /**
     * Cancel the running subprocess via SIGTERM-then-SIGKILL on the whole
     * process group (SC-B), then unlink any MCP spill file (CR-A).
     *
     * Idempotent: safe to call when the subprocess has already exited and safe
     * to call when no spill file was created. Errors from `process.kill` are
     * swallowed (`ESRCH` means the group is already gone).
     */
    async cancel() {
        const child = this.subprocess;
        if (child !== null &&
            child.exitCode === null &&
            child.signalCode === null &&
            child.pid !== undefined) {
            // detached:true on POSIX makes PID == PGID. Signal the negative pgid
            // so every MCP child the engine launched receives the same signal.
            const pgid = child.pid;
            try {
                process.kill(-pgid, "SIGTERM");
            }
            catch {
                // ESRCH: group already dead — ignore.
            }
            await waitForExitOrTimeout(child, SIGKILL_GRACE_MS);
            if (child.exitCode === null && child.signalCode === null) {
                try {
                    process.kill(-pgid, "SIGKILL");
                }
                catch {
                    // ESRCH — ignore.
                }
            }
        }
        if (this.mcpSpillPath !== null) {
            const path = this.mcpSpillPath;
            this.mcpSpillPath = null;
            await cleanupSpillFile(path);
        }
    }
    /** Graceful shutdown — alias for `cancel()` (D3). */
    async dispose() {
        await this.cancel();
    }
}
