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
export { AaaError, SessionHandle } from "./session.js";
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
export { Transport } from "./transport.js";
/** @public */
export type { TransportOptions, ExitInfo } from "./transport.js";

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
import type { DisplayEvent } from "./session.js";
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
  providerOverride?: string;
  /**
   * Mid-turn approval callback.
   *
   * **NOT SUPPORTED IN v1.** Passing a non-null `onRequest` throws
   * `AaaError(approval_not_supported_in_v1)` at spawnAgent() time. The v1 wire
   * is Mode A (per-turn subprocess); there is no mid-turn host channel.
   */
  approval?: {
    onRequest: (req: unknown) => Promise<ApprovalResponse>;
    timeoutMs: number;
  };
  display?: {
    onEvent?: (event: DisplayEvent) => void;
    subagentEvents?: "all" | "none";
  };
  /**
   * Optional MCP servers. Spilled to a 0600 tmpfile per submit and forwarded
   * to the engine via the `AMPLIFIER_MCP_CONFIG` env var injected into the
   * subprocess environment. The former `--mcp-config-path` argv flag was
   * removed; `tool-mcp` reads the env var natively via its config-discovery
   * priority chain.
   */
  mcpServers?: Record<string, McpServerConfig>;
  /** Per-submit timeout in ms (default: 10 minutes). */
  timeoutMs?: number;
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
  if (params.approval?.onRequest !== undefined) {
    throw new AaaError(
      "approval_not_supported_in_v1",
      "Mid-turn approval callbacks (params.approval.onRequest) are not supported in v1. " +
        "The Mode A wire has no mid-turn request channel. The bundle's hooks-approval mount " +
        "is the v1 policy point — auto-approve by default, configurable per-tool via the " +
        "bundle's hooks-approval default-mode and gating settings. To customize approval " +
        "policy in v1, configure the bundle; do not pass an onRequest callback. " +
        "Mid-turn callbacks will return in v1.x — track WG-4 in amendment §6.",
      { classification: "protocol", severity: "error" },
    );
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
    ...(params.providerOverride !== undefined
      ? { providerOverride: params.providerOverride }
      : {}),
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    ...(params.timeoutMs !== undefined ? { timeoutMs: params.timeoutMs } : {}),
  });
}
