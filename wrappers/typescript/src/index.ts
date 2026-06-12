/**
 * amplifier-agent-client-ts — public entry point.
 *
 * Exports the locked public API from design §8.2, narrowed to Mode A v2
 * (amendment §5). `spawnAgent` is synchronous-in-spirit: it validates
 * parameters, resolves the engine binary path, builds the subprocess
 * environment, and constructs a `SessionHandle`. **No subprocess is spawned
 * at spawn-time** — the engine is launched per `submit()` (amendment §5.2).
 */

// Re-export public types and classes from sub-modules.
export { AaaError, SessionHandle, DEFAULT_TIMEOUT_MS } from "./session.js";
export type {
  DisplayEvent,
  EngineInfo,
  SessionHandleParams,
} from "./session.js";
export type { ApprovalResponse } from "./approval.js";
export type { EngineVersionPayload } from "./spawn.js";

// ---------------------------------------------------------------------------
// Public re-exports of wrapper internals (Issue #5).
//
// These helpers and their associated types are part of the wrapper's
// supported public surface. They are useful to host authors who want to:
//   - Inspect the argv the wrapper would emit (`assembleArgv`)
//   - Inject their own subprocess factory (`runChildProcess` + spawn helpers)
//   - Probe the engine binary themselves (`resolveBinaryPath`,
//     `probeEngineVersion`, `buildEnv`)
//   - Drive the NDJSON event pipeline manually (`Transport`,
//     `parseNdjsonStream`)
//   - Reuse the same protocol-version comparison the wrapper uses
//     (`checkProtocolVersion`)
//   - Parse a captured run-output payload (`parseRunOutput`)
//
// All exports below are annotated `@public` in their defining module.
// ---------------------------------------------------------------------------

/** @public */
export { assembleArgv } from "./argv-builder.js";
/** @public */
export type { AssembleArgvInput } from "./argv-builder.js";

/** @public */
export { listModels, ListModelsError } from "./list-models.js";
/** @public */
export type {
  ListModelsParams,
  ModelInfo,
  ModelsListEnvelope,
} from "./list-models.js";

/** @public */
export { resolveMcpConfigPath, cleanupSpillFile } from "./mcp-spill.js";
/** @public */
export type { McpSpillResult } from "./mcp-spill.js";

/** @public */
export {
  resolveBinaryPath,
  buildEnv,
  probeEngineVersion,
  DEFAULT_ALLOWLIST,
  BLOCKED_ENV_KEYS,
} from "./spawn.js";
/** @public */
export type {
  ResolveBinaryPathOptions,
  BuildEnvOptions,
} from "./spawn.js";

/** @public */
export { Transport, parseNdjsonStream } from "./transport.js";
/** @public */
export type {
  TransportOptions,
  ExitInfo,
  ParseNdjsonStreamOptions,
} from "./transport.js";

/** @public */
export { checkProtocolVersion } from "./version.js";
/** @public */
export type {
  VersionCheckResult,
  VersionCheckOk,
  VersionCheckFail,
  CheckProtocolVersionOptions,
} from "./version.js";

/** @public */
export { parseRunOutput, STDERR_TAIL_BYTES } from "./run-output-parser.js";
/** @public */
export type { SubprocessOutcome } from "./run-output-parser.js";

/** @public */
export { makeApprovalHandler } from "./approval.js";
/** @public */
export type {
  ApprovalAdapter,
  ApprovalRequest,
  ApprovalHandler,
} from "./approval.js";

// Internal imports used by spawnAgent().
import { AaaError, SessionHandle } from "./session.js";
import type { DisplayEvent, ChildProcessFactory } from "./session.js";
import type { ApprovalResponse } from "./approval.js";
import {
  resolveBinaryPath,
  buildEnv,
  probeEngineVersion,
  DEFAULT_ALLOWLIST,
} from "./spawn.js";
import { checkProtocolVersion } from "./version.js";
import type { McpServerConfig } from "./types.js";

// Re-export the MCP/host wire types for callers who construct SpawnAgentParams.
export type { McpServerConfig } from "./types.js";
/** @public */
export type { ChildProcessFactory } from "./session.js";

/**
 * The protocol version that this TypeScript wrapper requires.
 * Forwarded to the engine via `--protocol-version` on every `submit()` and
 * checked at `spawnAgent()` time against the engine's reported protocol
 * version (see Issue #9 — `checkProtocolVersion()` is wired into the init
 * path so skew fails fast wrapper-side before any subprocess spawn).
 */
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0";

// ---------------------------------------------------------------------------
// SpawnAgentParams — locked public API (design §8.2, amended for Mode A v2)
// ---------------------------------------------------------------------------

/** Parameters for spawnAgent(). Signature is locked verbatim by design §8.2. */
export interface SpawnAgentParams {
  /** 'burst' reserved; throws AaaError(lifecycle_unsupported) at runtime. */
  lifecycle: "one-shot";
  sessionId: string;
  resume?: boolean;
  cwd?: string;
  env?: { allowlist: string[]; extra?: Record<string, string> };
  /**
   * Approval policy (Issue #10).
   *
   * Two shapes are accepted:
   *
   * 1. `{ mode: 'yes' | 'no' | 'prompt' }` — the static-policy shape.
   *    Maps to engine argv:
   *      - `'yes'`    → `-y` (auto-allow every tool call)
   *      - `'no'`     → `-n` (auto-deny every tool call)
   *      - `'prompt'` → emit no flag; the engine falls back to
   *                     `host_config.approval.mode` or the bundle's
   *                     TTY-based default. This is how a host hands
   *                     policy resolution back to the engine.
   *
   *    Engine compatibility: requires `amplifier-agent >= 0.4.0`
   *    (PR #34 added `host_config.approval.mode`).
   *
   * 2. `{ onRequest, timeoutMs }` — the legacy mid-turn callback shape.
   *    **NOT SUPPORTED IN v1.** Passing a non-null `onRequest` still
   *    throws `AaaError(approval_not_supported_in_v1)`. The v1 wire has
   *    no mid-turn host channel.
   *
   * When unset, the wrapper defaults to `mode: 'yes'` for backward
   * compatibility with pre-0.6 behaviour (the wrapper unconditionally
   * emitted `-y`).
   */
  approval?: {
    mode?: "yes" | "no" | "prompt";
    onRequest?: (req: unknown) => Promise<ApprovalResponse>;
    timeoutMs?: number;
  };
  display?: {
    onEvent?: (event: DisplayEvent) => void;
    subagentEvents?: "all" | "none";
  };
  /**
   * Stderr display mode forwarded to the engine via `--display <mode>`.
   *
   * Required to be `"ndjson"` for hosts consuming structured wire events via
   * `display.onEvent`. The engine's default (`text`) emits human-readable
   * `[type] summary` lines that the wrapper's `parseNdjsonStream` consumer
   * cannot decode — `display.onEvent` stays silent and structured fields
   * (cost, model, cache token counts, llm duration, etc.) never reach the
   * host.
   *
   * - `"ndjson"` — engine emits one JSON-RPC notification per line on stderr.
   *   Wrapper's `parseNdjsonStream.onJson` callback dispatches them as typed
   *   `display.onEvent({type: "notification", method, params})` events.
   * - `"text"` — engine emits human-readable text. Only useful for direct
   *   CLI use.
   * - omitted — wrapper emits no `--display` flag. Engine defaults to `text`,
   *   matching pre-existing behavior. Use this for compatibility with older
   *   engines that don't accept `--display`.
   *
   * Engine compatibility: requires `amplifier-agent` engine with the
   * `--display` flag (added alongside `JsonDisplaySystem`). Older engines
   * fail with a click "no such option" error if this is set.
   */
  displayMode?: "text" | "ndjson";
  /**
   * Workspace name for isolating session state by project. Forwarded to the
   * engine via `--workspace <name>`. When unset, the engine auto-derives a
   * slug from the cwd basename + 8-char sha256 of the resolved cwd path.
   *
   * Hosts that manage multiple agents per process (e.g. paperclip's
   * amplifier-local adapter, running multiple agents per company) should
   * set this so each agent's transcripts land in a separate directory
   * under `~/.amplifier-agent/state/workspaces/<workspace>/sessions/<id>/`.
   *
   * Must satisfy the engine's slug grammar `[a-z0-9][a-z0-9-]{0,63}`. The
   * engine validates and rejects invalid slugs with `argv_workspace_invalid`.
   */
  workspace?: string;
  /**
   * Optional MCP servers. Spilled to a 0600 tmpfile per submit and forwarded
   * to the engine via the `AMPLIFIER_MCP_CONFIG` env var injected into the
   * subprocess environment. The former `--mcp-config-path` argv flag was
   * removed; `tool-mcp` reads the env var natively via its config-discovery
   * priority chain.
   */
  mcpServers?: Record<string, McpServerConfig>;
  /**
   * Per-submit timeout in ms. No timeout is applied unless a positive value
   * is provided; `undefined` or `0` disables the wall-clock hang timer.
   * Pass `DEFAULT_TIMEOUT_MS` to opt into the original 10-minute cap.
   */
  timeoutMs?: number;
  /**
   * Path to a host config file (Issue #1). Forwarded to the engine via
   * `--config <configPath>` so the engine's host_config layer
   * (approval mode, MCP servers, provider defaults,
   * `allowProtocolSkew`, etc.) is composed from the file the host
   * already manages.
   *
   * Mirrors the engine's `single_turn --config` flag (engine PR #27 /
   * v0.4.0). When unset, the engine's resolution order applies (env
   * `AMPLIFIER_AGENT_CONFIG`, then `~/.amplifier-agent/config/host_config.json`).
   *
   * @public
   */
  configPath?: string;
  /**
   * Bypass the wrapper-side protocol-version check (Issue #9).
   *
   * Default `false`: `spawnAgent()` probes the engine's protocol version once
   * during initialization and rejects with `AaaError(protocol_version_mismatch)`
   * when it differs from `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`. Setting this to
   * `true` skips the check and lets the engine run regardless — useful for
   * exploratory work against pre-release engine versions, but unsafe by default.
   *
   * Mirrors the engine-side `host_config.allowProtocolSkew` knob.
   */
  allowProtocolSkew?: boolean;

  // ------------------------------------------------------------------
  // Test-only injection points (undocumented in public API).
  // ------------------------------------------------------------------
  /** Replaces the real resolveBinaryPath() call. */
  _binaryResolver?: () => string;
  /**
   * Replaces the real probeEngineVersion() call (Issue #9 + #7). When set,
   * `spawnAgent()` invokes this factory instead of spawning
   * `<binaryPath> version --json`. Reserved for tests and host-side stubs.
   */
  _engineVersionProbe?: () => Promise<{
    version: string;
    protocolVersion: string;
    bundleDigest?: string;
  }>;

  // ------------------------------------------------------------------
  // Public injection points (Issue #3).
  // ------------------------------------------------------------------
  /**
   * Override the subprocess factory used inside `SessionHandle.submit()`
   * (Issue #3). When set, the wrapper invokes this factory in place of
   * `child_process.spawn`. Useful for sandboxing, harness wrapping, or
   * test doubles.
   *
   * @public
   */
  runChildProcess?: ChildProcessFactory;
}

// ---------------------------------------------------------------------------
// spawnAgent() — locked public entry point (Mode A v2)
// ---------------------------------------------------------------------------

/**
 * Compose all internal components into the single public entry point.
 *
 * Mode A v2 flow (amendment §5):
 *  1. Guard: lifecycle must be 'one-shot' (D10).
 *  2. Reject `approval.onRequest !== undefined` (SC-C — v1 has no mid-turn channel).
 *  3. Resolve engine binary path (or inject via `_binaryResolver`).
 *  4. Build subprocess environment via `buildEnv`.
 *  5. Return `new SessionHandle(params)` — **NO subprocess is spawned here**.
 *
 * The engine is launched per `submit()` (amendment §5.2). `agent/initialize`
 * is gone; protocol-version handshake moves to argv at submit-time. Engine
 * metadata (`engineVersion`, `bundleDigest`) is populated lazily once the
 * first envelope arrives (TODO: Task-9 wires this from `parseRunOutput`).
 */
export async function spawnAgent(params: SpawnAgentParams): Promise<SessionHandle> {
  // SC-C: reject mid-turn approval callback before any other work. The Mode A
  // wire has no mid-turn request channel; warning-only acceptance would ship
  // silent auto-allow to a host author who believed their callback was wired.
  // Issue #10: `approval.mode` is the supported policy hook in v1; `onRequest`
  // remains unsupported until a mid-turn channel returns (track WG-4).
  if (params.approval?.onRequest !== undefined) {
    throw new AaaError(
      "approval_not_supported_in_v1",
      "Mid-turn approval callbacks (params.approval.onRequest) are not supported in v1. " +
        "The Mode A wire has no mid-turn request channel. Use the static-policy shape " +
        "`approval: { mode: 'yes' | 'no' | 'prompt' }` instead — it maps to engine argv " +
        "(`-y` / `-n`) and to `host_config.approval.mode`. Mid-turn callbacks will return " +
        "in v1.x — track WG-4 in amendment §6.",
      { classification: "protocol", severity: "error" },
    );
  }
  // Issue #10: validate the static-policy shape if present.
  let approvalModeArg: "yes" | "no" | "prompt" | undefined;
  if (params.approval?.mode !== undefined) {
    const m = params.approval.mode;
    if (m !== "yes" && m !== "no" && m !== "prompt") {
      throw new AaaError(
        "invalid_approval_mode",
        `params.approval.mode must be 'yes', 'no', or 'prompt' (got ${JSON.stringify(m)})`,
        { classification: "protocol", severity: "error" },
      );
    }
    approvalModeArg = m;
  }

  // 1. Lifecycle guard (D10).
  if (params.lifecycle !== "one-shot") {
    throw new AaaError(
      "lifecycle_unsupported",
      `lifecycle '${String(params.lifecycle)}' is not supported in v1; ` +
        `only 'one-shot' is supported. 'burst' is reserved for a future minor version.`,
    );
  }

  // 2. Resolve binary path.
  let binaryPath: string;
  if (params._binaryResolver) {
    binaryPath = params._binaryResolver();
  } else {
    try {
      binaryPath = resolveBinaryPath({
        env: process.env as Record<string, string | undefined>,
      });
    } catch (e: unknown) {
      const msg = (e as Error).message ?? "binary not found";
      throw new AaaError("binary_not_found", msg);
    }
  }

  // 3. Build subprocess environment.
  const allowlist = params.env?.allowlist ?? DEFAULT_ALLOWLIST;
  const extra = params.env?.extra ?? {};
  const subprocessEnv = buildEnv({
    processEnv: process.env as Record<string, string | undefined>,
    allowlist,
    extra,
  });

  // 4. Issue #9: probe the engine binary for its protocol version and run
  //    `checkProtocolVersion()` BEFORE constructing a SessionHandle. This is
  //    a single `amplifier-agent version --json` roundtrip during init — far
  //    cheaper than discovering a mismatch on the first `submit()` after the
  //    engine has done its full bundle-load dance. The probe result is also
  //    cached on the handle for `getEngineInfo()` (Issue #7).
  //
  //    Callers can:
  //      - Inject a synthetic probe via `_engineVersionProbe` (tests).
  //      - Bypass the check entirely with `allowProtocolSkew: true`.
  let engineVersionPayload: { version: string; protocolVersion: string; bundleDigest?: string };
  try {
    if (params._engineVersionProbe) {
      engineVersionPayload = await params._engineVersionProbe();
    } else {
      engineVersionPayload = await probeEngineVersion(binaryPath, subprocessEnv);
    }
  } catch (e: unknown) {
    // Probe failure is non-fatal when skew is allowed: fall back to empty
    // metadata. Otherwise surface it as a typed error.
    if (params.allowProtocolSkew === true) {
      engineVersionPayload = { version: "", protocolVersion: "" };
    } else {
      const msg = (e as Error).message ?? "engine version probe failed";
      throw new AaaError(
        "engine_probe_failed",
        `Could not probe engine binary at ${binaryPath} for protocol version: ${msg}`,
        { classification: "transport", severity: "error" },
      );
    }
  }

  const check = checkProtocolVersion({
    wrapper: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    engine: engineVersionPayload.protocolVersion,
    allowSkew: params.allowProtocolSkew === true,
  });
  if (!check.ok) {
    throw new AaaError(check.code, check.remediation, {
      classification: "protocol",
      severity: "error",
    });
  }

  // 5. Return a SessionHandle. NO subprocess spawned here — the engine is
  //    launched per submit() (amendment §5.2). Skew override now lives in
  //    `host_config.allowProtocolSkew: true` in the host config file (engine
  //    PR #27); the wrapper no longer forwards an argv flag for it.
  return new SessionHandle({
    binaryPath,
    sessionId: params.sessionId,
    subprocessEnv,
    ...(params.resume !== undefined ? { resume: params.resume } : {}),
    ...(params.cwd !== undefined ? { cwd: params.cwd } : {}),
    ...(params.mcpServers !== undefined ? { mcpServers: params.mcpServers } : {}),
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    ...(params.timeoutMs !== undefined ? { timeoutMs: params.timeoutMs } : {}),
    ...(params.runChildProcess !== undefined
      ? { runChildProcess: params.runChildProcess }
      : {}),
    // Issue #1: forward host config path so assembleArgv can emit
    // --config <path>.
    ...(params.configPath !== undefined ? { configPath: params.configPath } : {}),
    // Issue #10: forward the validated approval mode so assembleArgv can
    // emit -y / -n / nothing.
    ...(approvalModeArg !== undefined ? { approvalMode: approvalModeArg } : {}),
    // Forward displayMode so assembleArgv can emit `--display ndjson` (or
    // text). Required for hosts consuming structured wire events via the
    // wrapper's parseNdjsonStream → display.onEvent path; without ndjson
    // the engine emits human-readable text that the NDJSON consumer
    // cannot decode.
    ...(params.displayMode !== undefined ? { displayMode: params.displayMode } : {}),
    // Forward workspace so assembleArgv can emit `--workspace <slug>`.
    // When omitted, the engine auto-derives a slug from cwd basename +
    // sha256 — fine for single-agent hosts, but multi-agent hosts (like
    // paperclip) should set this so each agent gets an isolated state dir
    // instead of all sharing the same cwd-derived workspace.
    ...(params.workspace !== undefined ? { workspace: params.workspace } : {}),
    // Issue #4: thread display.onEvent through so SessionHandle can
    // dispatch parsed NDJSON wire events to it.
    ...(params.display !== undefined ? { display: params.display } : {}),
    // Issue #7: persist engine metadata resolved during the probe.
    engineVersion: engineVersionPayload.version,
    bundleDigest: engineVersionPayload.bundleDigest ?? "",
  });
}
