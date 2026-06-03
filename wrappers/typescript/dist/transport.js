/**
 * NDJSON subprocess transport.
 *
 * Spawns a child process and exchanges JSON frames over its stdio:
 * - onFrame(cb): register a callback to receive parsed JSON objects from stdout
 * - send(obj): write a JSON frame (NDJSON) to stdin
 * - terminate(): send SIGTERM and await process exit
 *
 * Defensive requirement (MCP-style tolerance): non-JSON stdout lines are
 * logged to the stderr sink (or process.stderr) and dropped silently.
 *
 * No JSON-RPC semantics here — that is Task 6.
 */
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
/**
 * Consume an NDJSON stream line-by-line, dispatching each parseable JSON
 * line to `onJson` and each non-JSON line to `onNonJson` (or silently
 * dropping it).
 *
 * Implements the same parsing model that {@link Transport} uses internally,
 * but exposed as a standalone helper so SessionHandle can wire it onto
 * the engine subprocess's stderr stream without owning the spawn.
 *
 * **Wire contract:** the engine emits one JSON object per line on stderr
 * for each wire-protocol notification (Issue #2 / #6). Each parsed object
 * is delivered verbatim to `onJson`; the caller decides how to interpret
 * the `method`/`params` shape.
 *
 * Returns a Promise that resolves when the underlying stream emits `end`.
 *
 * @public
 */
export function parseNdjsonStream(stream, options) {
    return new Promise((resolve, reject) => {
        const rl = createInterface({ input: stream });
        rl.on("line", (line) => {
            const trimmed = line.trim();
            if (trimmed.length === 0)
                return;
            try {
                const obj = JSON.parse(trimmed);
                if (typeof obj === "object" && obj !== null) {
                    options.onJson(obj);
                }
                else if (options.onNonJson) {
                    // JSON-parseable but not an object (e.g. a bare number/string).
                    options.onNonJson(line);
                }
            }
            catch {
                if (options.onNonJson)
                    options.onNonJson(line);
            }
        });
        rl.on("close", () => resolve());
        stream.on("error", (err) => reject(err));
    });
}
export class Transport {
    opts;
    proc = null;
    frameCallbacks = [];
    exitPromise = null;
    constructor(opts) {
        this.opts = opts;
    }
    /**
     * Spawn the child process and start reading its stdout/stderr.
     *
     * After this resolves, send() and terminate() are available.
     * onFrame() callbacks may be registered before or after spawn().
     */
    async spawn() {
        const proc = spawn(this.opts.command, this.opts.args, {
            stdio: ["pipe", "pipe", "pipe"],
            // Merge caller-supplied env on top of the current process environment.
            env: { ...process.env, ...this.opts.env },
            cwd: this.opts.cwd,
        });
        this.proc = proc;
        // exitPromise resolves after the child process AND all its stdio streams
        // have closed.  The 'close' event fires AFTER readline has processed all
        // buffered lines, so frames are guaranteed to be delivered before the
        // promise resolves.
        this.exitPromise = new Promise((resolve) => {
            proc.on("close", (code, signal) => resolve({ code, signal }));
        });
        // Read stdout line by line; parse JSON; dispatch to registered callbacks.
        const stdoutRl = createInterface({ input: proc.stdout });
        stdoutRl.on("line", (line) => {
            try {
                const obj = JSON.parse(line);
                for (const cb of this.frameCallbacks) {
                    cb(obj);
                }
            }
            catch {
                // Non-JSON line: log to stderr sink and drop silently.
                const msg = `[transport] non-JSON stdout line dropped: ${line}`;
                if (this.opts.stderr) {
                    this.opts.stderr(msg);
                }
                else {
                    process.stderr.write(msg + "\n");
                }
            }
        });
        // Drain child stderr to the optional sink.
        const stderrRl = createInterface({ input: proc.stderr });
        stderrRl.on("line", (line) => {
            if (this.opts.stderr) {
                this.opts.stderr(line);
            }
        });
    }
    /**
     * Register a callback that is invoked with each parsed JSON frame received
     * on the child's stdout.  Callbacks may be registered before or after spawn().
     */
    onFrame(cb) {
        this.frameCallbacks.push(cb);
    }
    /**
     * Serialize obj as a single NDJSON line and write it to the child's stdin.
     * Pattern: json.dumps(obj) + '\n' (matches engine's NDJSON write side).
     */
    async send(obj) {
        return new Promise((resolve, reject) => {
            const data = JSON.stringify(obj) + "\n";
            this.proc.stdin.write(data, (err) => {
                if (err)
                    reject(err);
                else
                    resolve();
            });
        });
    }
    /**
     * Send SIGTERM to the child process and await its exit.
     *
     * Returns ExitInfo with the final exit code and/or signal.
     * Safe to call even if the process has already exited — will just
     * return the already-resolved exit info.
     */
    async terminate() {
        if (this.proc) {
            try {
                this.proc.kill("SIGTERM");
            }
            catch {
                // Process may have already exited; kill() failing is expected.
            }
        }
        return this.exitPromise;
    }
}
