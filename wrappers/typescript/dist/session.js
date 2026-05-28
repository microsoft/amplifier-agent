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
import { spawn as childSpawn } from "node:child_process";
import { assembleArgv } from "./argv-builder.js";
import { resolveMcpConfigPath, cleanupSpillFile } from "./mcp-spill.js";
import { parseRunOutput, STDERR_TAIL_BYTES } from "./run-output-parser.js";
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
/** Default subprocess timeout: 10 minutes. */
const DEFAULT_TIMEOUT_MS = 10 * 60 * 1000;
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
            // engineVersion / bundleDigest are no longer probed up-front under Mode
            // A v2 — they will be populated from the JSON envelope's `metadata`
            // field once it arrives (TODO: Task-9 wires this from parseRunOutput).
            engineVersion: "",
            bundleDigest: "",
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
     *   (ii)  CR-A: resolve `--mcp-config-path` (always spill to tmpfile);
     *   (iii) build argv via `assembleArgv`;
     *   (iv)  SC-B: spawn with `detached:true` so PID == PGID for group signals;
     *   (v)   accumulate stdout/stderr from chunks;
     *   (vi)  start a 2s activity ticker → queue;
     *   (vii) race `exitPromise` vs `timeoutPromise`;
     *         on timeout: cancel(), synthesize `engine_hung`;
     *         on exit:    parseRunOutput({stdout, stderr, exitCode});
     *   (viii) cleanup spill file after exit;
     *   (ix)  drain queue until the final event is yielded.
     */
    async *makeIterable(prompt) {
        // (i) SC-1: yield init synchronously, BEFORE any async work.
        yield { type: "init", sessionId: this.params.sessionId };
        // (ii) CR-A: resolve --mcp-config-path (always spill to 0600 tmpfile).
        // Cast through `unknown`: McpServerConfig is the schema-validated wire type
        // (no index signature), while resolveMcpConfigPath's McpServerLike has an
        // open index signature. The two are runtime-compatible but TS rightly
        // rejects assignability without the cast.
        const spill = await resolveMcpConfigPath((this.params.mcpServers ?? null), this.params.sessionId);
        this.mcpSpillPath = spill.configPath;
        // (iii) build argv (pure function — no I/O).
        const argv = assembleArgv({
            sessionId: this.params.sessionId,
            prompt,
            protocolVersion: this.params.protocolVersion,
            resume: this.params.resume,
            cwd: this.params.cwd,
            providerOverride: this.params.providerOverride,
            mcpConfigPath: spill.configPath ?? undefined,
            hostCapabilities: this.params.hostCapabilities,
            envAllowlist: this.params.envAllowlist,
            envExtra: this.params.envExtra,
            allowProtocolSkew: this.params.allowProtocolSkew,
        });
        // (iv) SC-B: spawn detached → new session group → PID == PGID.
        const child = childSpawn(this.params.binaryPath, argv, {
            detached: true,
            stdio: ["ignore", "pipe", "pipe"],
            env: this.params.subprocessEnv,
            ...(this.params.cwd !== undefined ? { cwd: this.params.cwd } : {}),
        });
        this.subprocess = child;
        // (v) accumulate stdout/stderr from chunks.
        let stdoutBuf = "";
        let stderrBuf = "";
        child.stdout?.on("data", (chunk) => {
            stdoutBuf += typeof chunk === "string" ? chunk : chunk.toString("utf-8");
        });
        child.stderr?.on("data", (chunk) => {
            stderrBuf += typeof chunk === "string" ? chunk : chunk.toString("utf-8");
        });
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
        // (vii) race exit vs timeout.
        const timeoutMs = this.params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
        const timeoutHandle = setTimeout(() => {
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
        child.once("exit", (code, _signal) => {
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
