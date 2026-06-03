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

import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
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
export function parseNdjsonStream(
  stream: Readable,
  options: ParseNdjsonStreamOptions,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const rl = createInterface({ input: stream });
    rl.on("line", (line) => {
      const trimmed = line.trim();
      if (trimmed.length === 0) return;
      try {
        const obj = JSON.parse(trimmed) as unknown;
        if (typeof obj === "object" && obj !== null) {
          options.onJson(obj as Record<string, unknown>);
        } else if (options.onNonJson) {
          // JSON-parseable but not an object (e.g. a bare number/string).
          options.onNonJson(line);
        }
      } catch {
        if (options.onNonJson) options.onNonJson(line);
      }
    });
    rl.on("close", () => resolve());
    stream.on("error", (err) => reject(err));
  });
}

export class Transport {
  private readonly opts: TransportOptions;
  private proc: ChildProcess | null = null;
  private readonly frameCallbacks: Array<(obj: unknown) => void> = [];
  private exitPromise: Promise<ExitInfo> | null = null;

  constructor(opts: TransportOptions) {
    this.opts = opts;
  }

  /**
   * Spawn the child process and start reading its stdout/stderr.
   *
   * After this resolves, send() and terminate() are available.
   * onFrame() callbacks may be registered before or after spawn().
   */
  async spawn(): Promise<void> {
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
    this.exitPromise = new Promise<ExitInfo>((resolve) => {
      proc.on("close", (code, signal) => resolve({ code, signal }));
    });

    // Read stdout line by line; parse JSON; dispatch to registered callbacks.
    const stdoutRl = createInterface({ input: proc.stdout! });
    stdoutRl.on("line", (line) => {
      try {
        const obj = JSON.parse(line) as unknown;
        for (const cb of this.frameCallbacks) {
          cb(obj);
        }
      } catch {
        // Non-JSON line: log to stderr sink and drop silently.
        const msg = `[transport] non-JSON stdout line dropped: ${line}`;
        if (this.opts.stderr) {
          this.opts.stderr(msg);
        } else {
          process.stderr.write(msg + "\n");
        }
      }
    });

    // Drain child stderr to the optional sink.
    const stderrRl = createInterface({ input: proc.stderr! });
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
  onFrame(cb: (obj: unknown) => void): void {
    this.frameCallbacks.push(cb);
  }

  /**
   * Serialize obj as a single NDJSON line and write it to the child's stdin.
   * Pattern: json.dumps(obj) + '\n' (matches engine's NDJSON write side).
   */
  async send(obj: unknown): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const data = JSON.stringify(obj) + "\n";
      this.proc!.stdin!.write(data, (err) => {
        if (err) reject(err);
        else resolve();
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
  async terminate(): Promise<ExitInfo> {
    if (this.proc) {
      try {
        this.proc.kill("SIGTERM");
      } catch {
        // Process may have already exited; kill() failing is expected.
      }
    }
    return this.exitPromise!;
  }
}
