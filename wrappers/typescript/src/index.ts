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

// Internal imports used by spawnAgent().
import { AaaError, SessionHandle } from "./session.js";
import type { DisplayEvent } from "./session.js";
import type { ApprovalResponse } from "./approval.js";
import { resolveBinaryPath, buildEnv, DEFAULT_ALLOWLIST } from "./spawn.js";
import type { McpServerConfig } from "./types.js";

// Re-export the MCP/host wire types for callers who construct SpawnAgentParams.
export type { McpServerConfig } from "./types.js";

/**
 * The protocol version that this TypeScript wrapper requires.
 * Forwarded to the engine via `--protocol-version` on every `submit()`.
 */
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.2.0";

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

  // ------------------------------------------------------------------
  // Test-only injection points (undocumented in public API).
  // ------------------------------------------------------------------
  /** Replaces the real resolveBinaryPath() call. */
  _binaryResolver?: () => string;
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

  // 4. Return a SessionHandle. NO subprocess spawned here — the engine is
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
