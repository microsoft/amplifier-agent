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
import type { ChildProcess, SpawnOptions } from "node:child_process";

import { assembleArgv } from "./argv-builder.js";
import { resolveMcpConfigPath, cleanupSpillFile } from "./mcp-spill.js";
import { parseRunOutput, STDERR_TAIL_BYTES } from "./run-output-parser.js";
import { parseNdjsonStream } from "./transport.js";
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
export type ChildProcessFactory = (
  command: string,
  args: readonly string[],
  options: SpawnOptions,
) => ChildProcess;

/**
 * A display event yielded by `SessionHandle.submit()`.
 *
 * Mode A v2 (CR-C, amendment §5.2): a discriminated union narrow enough that
 * every variant's payload is exhaustively typed. The fields removed from the
 * pre-amendment shape (`turnId`, `parentTurnId`, `synthesized`, `payload`)
 * cannot be meaningfully populated on the Mode A wire.
 */
export type DisplayEvent =
  | { type: "init"; sessionId: string }
  | { type: "activity" }
  | { type: "result"; text: string }
  | {
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
  | { type: "notification"; method: string; params: unknown };

/** Typed error for AaA wrapper lifecycle and protocol violations. */
export class AaaError extends Error {
  code: string;
  remediation?: string;
  classification?: "transport" | "protocol" | "engine" | "approval" | "unknown";
  severity?: "error" | "warning";
  correlationId?: string;
  stderrTail?: string;

  constructor(
    code: string,
    remediation?: string,
    opts?: {
      classification?: AaaError["classification"];
      severity?: AaaError["severity"];
      correlationId?: string;
      stderrTail?: string;
    },
  ) {
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
  /** Protocol version the wrapper speaks (e.g. "0.3.0"). */
  protocolVersion: string;
  /** Per-submit timeout in milliseconds. Defaults to 10 minutes. */
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
export function waitForExitOrTimeout(child: ChildProcess, ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
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
function stderrTailOf(stderr: string): string | undefined {
  if (!stderr) return undefined;
  if (stderr.length <= STDERR_TAIL_BYTES) return stderr;
  return stderr.slice(stderr.length - STDERR_TAIL_BYTES);
}

/** One-shot session handle that drives the engine subprocess. */
export class SessionHandle {
  private submitted = false;
  private subprocess: ChildProcess | null = null;
  private mcpSpillPath: string | null = null;
  private readonly engineInfo: EngineInfo;

  constructor(private readonly params: SessionHandleParams) {
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
  getEngineInfo(): EngineInfo {
    return this.engineInfo;
  }

  /**
   * Submit a prompt and return an AsyncIterable of DisplayEvents.
   *
   * One-shot per session (D10): throws AaaError on second call.
   */
  submit(prompt: string): AsyncIterable<DisplayEvent> {
    if (this.submitted) {
      throw new AaaError(
        "lifecycle_unsupported",
        "SessionHandle.submit() is one-shot per session (D10); already submitted",
      );
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
   *   (vii) race `exitPromise` vs `timeoutPromise`;
   *         on timeout: cancel(), synthesize `engine_hung`;
   *         on exit:    parseRunOutput({stdout, stderr, exitCode});
   *   (viii) cleanup spill file after exit;
   *   (ix)  drain queue until the final event is yielded.
   */
  private async *makeIterable(prompt: string): AsyncGenerator<DisplayEvent> {
    // (i) SC-1: yield init synchronously, BEFORE any async work.
    yield { type: "init", sessionId: this.params.sessionId };

    // (ii) CR-A: spill MCP servers to a 0600 tmpfile (configPath is null when
    // mcpServers is null/empty).
    // Cast through `unknown`: McpServerConfig is the schema-validated wire type
    // (no index signature), while resolveMcpConfigPath's McpServerLike has an
    // open index signature. The two are runtime-compatible but TS rightly
    // rejects assignability without the cast.
    const spill = await resolveMcpConfigPath(
      (this.params.mcpServers ?? null) as unknown as Parameters<
        typeof resolveMcpConfigPath
      >[0],
      this.params.sessionId,
    );
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
    });

    // Build the subprocess env. When we spilled an MCP config, set
    // AMPLIFIER_MCP_CONFIG so tool-mcp reads it natively via its
    // config-discovery priority chain. We copy the params env into a fresh
    // object so the stored SessionHandle env is never mutated.
    const subprocessEnv: Record<string, string> = {
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
    child.stdout?.on("data", (chunk: Buffer | string) => {
      stdoutBuf += typeof chunk === "string" ? chunk : chunk.toString("utf-8");
    });

    // Issue #2 / #6: wire `parseNdjsonStream` onto the child's stderr. The
    // engine emits one JSON object per line for each wire-protocol
    // notification (progress, result/delta, tool/started, etc.).
    // - JSON lines are parsed into `notification` DisplayEvents and
    //   dispatched to `params.display?.onEvent` (Issue #4).
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
          if (displayOnEvent) {
            const method =
              typeof obj.method === "string" ? obj.method : "unknown";
            const params =
              "params" in obj ? obj.params : obj;
            displayOnEvent({ type: "notification", method, params });
          }
        },
        onNonJson: (line) => {
          stderrBuf += line + "\n";
        },
      });
    }

    // Single-producer queue: activity ticks + the final event.
    type QueueItem = DisplayEvent | { _done: true };
    const queue: QueueItem[] = [];
    let wake: (() => void) | null = null;
    const push = (item: QueueItem): void => {
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
    const finalize = (ev: DisplayEvent): void => {
      if (finalized) return;
      finalized = true;
      push(ev);
      push({ _done: true });
    };

    // (vi) 2s activity ticker.
    const ticker = setInterval(() => {
      if (!finalized) push({ type: "activity" });
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

    child.once("exit", (code: number | null, _signal: NodeJS.Signals | null) => {
      clearTimeout(timeoutHandle);
      if (finalized) return;
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
    child.once("error", (err: NodeJS.ErrnoException) => {
      clearTimeout(timeoutHandle);
      if (finalized) return;
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
          const item = queue.shift() as QueueItem;
          if ("_done" in item) {
            return;
          }
          yield item;
        }
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
    } finally {
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
  async cancel(): Promise<void> {
    const child = this.subprocess;
    if (
      child !== null &&
      child.exitCode === null &&
      child.signalCode === null &&
      child.pid !== undefined
    ) {
      // detached:true on POSIX makes PID == PGID. Signal the negative pgid
      // so every MCP child the engine launched receives the same signal.
      const pgid = child.pid;
      try {
        process.kill(-pgid, "SIGTERM");
      } catch {
        // ESRCH: group already dead — ignore.
      }
      await waitForExitOrTimeout(child, SIGKILL_GRACE_MS);
      if (child.exitCode === null && child.signalCode === null) {
        try {
          process.kill(-pgid, "SIGKILL");
        } catch {
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
  async dispose(): Promise<void> {
    await this.cancel();
  }
}
