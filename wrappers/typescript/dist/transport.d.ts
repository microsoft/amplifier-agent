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
import type { Readable } from "node:stream";
export interface TransportOptions {
    /** Command to spawn (e.g. "cat", "sh"). */
    command: string;
    /** Arguments passed to the command. */
    args: string[];
    /**
     * Environment variable overrides merged on top of the current process env.
     * Pass {} to inherit the current environment without overrides.
     */
    env: Record<string, string>;
    /** Optional working directory for the child process. */
    cwd?: string;
    /**
     * Optional sink for stderr lines from the child and for non-JSON drop
     * warnings from the transport itself.  Defaults to process.stderr.
     */
    stderr?: (line: string) => void;
}
export interface ExitInfo {
    /** Process exit code, or null if the process was killed by a signal. */
    code: number | null;
    /** Signal name if the process was killed by a signal, otherwise null. */
    signal: string | null;
}
/**
 * Options for {@link parseNdjsonStream}.
 *
 * @public
 */
export interface ParseNdjsonStreamOptions {
    /**
     * Invoked once per line that parses cleanly as JSON. Failures in the
     * callback are NOT caught — the caller is responsible for not throwing
     * inside the dispatcher.
     */
    onJson: (obj: Record<string, unknown>) => void;
    /**
     * Invoked once per line that does NOT parse as JSON (e.g. plain stderr
     * text, partial frames). The line is delivered verbatim without the
     * trailing newline. Default: silently drop the line.
     */
    onNonJson?: (line: string) => void;
}
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
export declare function parseNdjsonStream(stream: Readable, options: ParseNdjsonStreamOptions): Promise<void>;
export declare class Transport {
    private readonly opts;
    private proc;
    private readonly frameCallbacks;
    private exitPromise;
    constructor(opts: TransportOptions);
    /**
     * Spawn the child process and start reading its stdout/stderr.
     *
     * After this resolves, send() and terminate() are available.
     * onFrame() callbacks may be registered before or after spawn().
     */
    spawn(): Promise<void>;
    /**
     * Register a callback that is invoked with each parsed JSON frame received
     * on the child's stdout.  Callbacks may be registered before or after spawn().
     */
    onFrame(cb: (obj: unknown) => void): void;
    /**
     * Serialize obj as a single NDJSON line and write it to the child's stdin.
     * Pattern: json.dumps(obj) + '\n' (matches engine's NDJSON write side).
     */
    send(obj: unknown): Promise<void>;
    /**
     * Send SIGTERM to the child process and await its exit.
     *
     * Returns ExitInfo with the final exit code and/or signal.
     * Safe to call even if the process has already exited — will just
     * return the already-resolved exit info.
     */
    terminate(): Promise<ExitInfo>;
}
