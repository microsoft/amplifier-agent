/**
 * amplifier-agent-client-ts — public entry point.
 *
 * Exports the locked public API from design §8.2.
 * spawnAgent() composes all internal components into one entry point.
 */

// Re-export public types and classes from sub-modules.
export { AaaError, SessionHandle, TERMINAL_NOTIFICATION } from "./session.js";
export type { DisplayEvent, EngineInfo, RpcLike, SessionDeps } from "./session.js";
export type { ApprovalAdapter, ApprovalResponse } from "./approval.js";
export type { DisplayAdapter } from "./display.js";
export type { EngineVersionPayload } from "./spawn.js";

// Internal imports used by spawnAgent().
import { AaaError, SessionHandle } from "./session.js";
import type { DisplayAdapter } from "./display.js";
import type { ApprovalAdapter, ApprovalResponse } from "./approval.js";
import type { DisplayEvent } from "./session.js";
import { Transport } from "./transport.js";
import type { TransportOptions, ExitInfo } from "./transport.js";
import { JsonRpcClient } from "./jsonrpc.js";
import {
  resolveBinaryPath,
  buildEnv,
  probeEngineVersion,
  DEFAULT_ALLOWLIST,
} from "./spawn.js";
import type { EngineVersionPayload } from "./spawn.js";
import { checkProtocolVersion } from "./version.js";
import type { McpServerConfig, HostCapabilities } from "./types.js";

// Re-export the MCP/host wire types for callers who construct SpawnAgentParams.
export type { McpServerConfig, HostCapabilities } from "./types.js";

/**
 * The protocol version that this TypeScript wrapper requires.
 * Used by the smoke test to verify that the package is correctly installed
 * and that the correct protocol version constant is exported.
 */
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.1.0";

// ---------------------------------------------------------------------------
// SpawnAgentParams — locked public API (design §8.2)
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
   *
   * For v1, configure approval policy at the bundle layer via hooks-approval's
   * default-mode and per-tool gating. Mid-turn callbacks return in v1.x — see
   * the amendment §6 WG-4 deferral nominees for the planned revival path.
   */
  approval?: {
    onRequest: (req: unknown) => Promise<ApprovalResponse>;
    timeoutMs: number;
  };
  display?: {
    onEvent?: (event: DisplayEvent) => void;
    subagentEvents?: "all" | "none";
  };
  /** Default false; opt out of D6 strict-refuse version check. */
  allowProtocolSkew?: boolean;
  /** Optional MCP servers to forward via initialize.params.mcpServers (A1). */
  mcpServers?: Record<string, McpServerConfig>;
  /** Optional host envelope forwarded via initialize.params.host (A1). */
  host?: { capabilities?: HostCapabilities };

  // ------------------------------------------------------------------
  // Test-only injection points (undocumented in public API).
  // ------------------------------------------------------------------
  /** Factory returning a transport-like object. Replaces the real Transport. */
  _transportFactory?: (opts: TransportOptions) => FakeableTransport;
  /** Replaces the real probeEngineVersion() call. */
  _versionProbe?: (
    binPath: string,
    env: Record<string, string>,
  ) => Promise<EngineVersionPayload>;
  /** Replaces the real resolveBinaryPath() call. */
  _binaryResolver?: () => string;
}

/**
 * Minimal transport interface needed by spawnAgent().
 * Implemented by the real Transport and by test FakeTransport objects.
 */
export interface FakeableTransport {
  spawn(): Promise<void>;
  send(obj: unknown): void;
  onFrame(cb: (obj: unknown) => void): void;
  terminate(): Promise<ExitInfo>;
}

// ---------------------------------------------------------------------------
// spawnAgent() — locked public entry point
// ---------------------------------------------------------------------------

/**
 * Compose all internal components into the single public entry point.
 *
 * Flow:
 *  1. Guard: lifecycle must be 'one-shot' (D10).
 *  2. Resolve binary path (or inject via _binaryResolver).
 *  3. Build subprocess environment via buildEnv.
 *  4. Probe engine version (or inject via _versionProbe).
 *  5. Check protocol version; throw on mismatch unless allowProtocolSkew.
 *  6. Spawn Transport (or inject via _transportFactory) running
 *     `amplifier-agent run --stdio`.
 *  7. Construct JsonRpcClient, register approval/request handler.
 *  8. Send `agent/initialize` with all params; capture result.
 *  9. Return SessionHandle with getEngineInfo() data.
 */
export async function spawnAgent(params: SpawnAgentParams): Promise<SessionHandle> {
  // SC-C: reject mid-turn approval callback before any subprocess work is done
  // (amendment §5.3). The Mode A wire has no mid-turn request channel; warning-
  // only acceptance would ship silent auto-allow to a host author who believed
  // their callback was wired up. Reject loudly instead. Mid-turn callbacks
  // return in v1.x — track WG-4 in amendment §6.
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

  // 4. Probe engine version.
  let versionPayload: EngineVersionPayload;
  if (params._versionProbe) {
    versionPayload = await params._versionProbe(binaryPath, subprocessEnv);
  } else {
    versionPayload = await probeEngineVersion(binaryPath, subprocessEnv);
  }

  // 5. Check protocol version (D6 strict-refuse).
  const allowSkew =
    params.allowProtocolSkew === true ||
    process.env["AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW"] === "1";
  const versionCheck = checkProtocolVersion({
    wrapper: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    engine: versionPayload.protocolVersion,
    allowSkew,
  });
  if (!versionCheck.ok) {
    throw new AaaError(versionCheck.code, versionCheck.remediation);
  }

  // 6. Spawn transport running `amplifier-agent run --stdio`.
  const transportOpts: TransportOptions = {
    command: binaryPath,
    args: ["run", "--stdio"],
    env: subprocessEnv,
    cwd: params.cwd,
  };

  const transport: FakeableTransport = params._transportFactory
    ? params._transportFactory(transportOpts)
    : (new Transport(transportOpts) as unknown as FakeableTransport);

  await transport.spawn();

  // 7. Construct JsonRpcClient with the spawned transport.
  // JsonRpcClient expects a TransportLike ({send, onFrame}); we create a thin
  // adapter shim so that the real Transport's async send() works without changing
  // its signature, and FakeableTransport's sync send() works too.
  const rpc = new JsonRpcClient({
    send(obj: unknown): void {
      // Intentionally ignore the returned Promise from async transports.
      void Promise.resolve(transport.send(obj));
    },
    onFrame(cb: (obj: unknown) => void): void {
      transport.onFrame(cb);
    },
  });

  // 8. Send agent/initialize.
  const capabilities: Record<string, unknown> = {};
  if (params.approval) {
    capabilities["approval"] = { actions: ["allow", "deny"] };
  }
  if (params.display) {
    capabilities["display"] = { events: ["*"] };
  }

  const initResult = (await rpc.call("agent/initialize", {
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    clientInfo: { name: "amplifier-agent-client-ts", version: "0.0.0" },
    capabilities,
    sessionId: params.sessionId,
    resume: params.resume,
    cwd: params.cwd,
    providerOverride: params.providerOverride,
  })) as {
    capabilities: Record<string, unknown>;
    serverInfo: { name: string; version: string };
    sessionState: { sessionId: string; resumed: boolean };
  };

  const effectiveSessionId = initResult.sessionState.sessionId;

  // Build adapters from params.
  const approvalAdapter: ApprovalAdapter | undefined = params.approval
    ? {
        onRequest: params.approval.onRequest,
        timeoutMs: params.approval.timeoutMs,
      }
    : undefined;

  const displayAdapter: DisplayAdapter | undefined = params.display
    ? {
        onEvent: params.display.onEvent,
        subagentEvents: params.display.subagentEvents,
      }
    : undefined;

  // 9. Return SessionHandle with engine info (D5).
  return new SessionHandle(
    rpc,
    {
      sessionId: effectiveSessionId,
      terminate: () => transport.terminate(),
      binaryPath,
      protocolVersion: versionPayload.protocolVersion,
      engineVersion: versionPayload.version,
      bundleDigest: versionPayload.bundleDigest ?? "",
    },
    approvalAdapter,
    displayAdapter,
  );
}
