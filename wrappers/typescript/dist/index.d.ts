/**
 * amplifier-agent-client-ts — public entry point.
 *
 * Exports the locked public API from design §8.2, narrowed to Mode A v2
 * (amendment §5). `spawnAgent` is synchronous-in-spirit: it validates
 * parameters, resolves the engine binary path, builds the subprocess
 * environment, and constructs a `SessionHandle`. **No subprocess is spawned
 * at spawn-time** — the engine is launched per `submit()` (amendment §5.2).
 */
export { AaaError, SessionHandle } from "./session.js";
export type { DisplayEvent, EngineInfo, SessionHandleParams, } from "./session.js";
export type { ApprovalResponse } from "./approval.js";
export type { EngineVersionPayload } from "./spawn.js";
import { SessionHandle } from "./session.js";
import type { DisplayEvent } from "./session.js";
import type { ApprovalResponse } from "./approval.js";
import type { McpServerConfig, HostCapabilities } from "./types.js";
export type { McpServerConfig, HostCapabilities } from "./types.js";
/**
 * The protocol version that this TypeScript wrapper requires.
 * Forwarded to the engine via `--protocol-version` on every `submit()`.
 */
export declare const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.2.0";
/** Parameters for spawnAgent(). Signature is locked verbatim by design §8.2. */
export interface SpawnAgentParams {
    /** 'burst' reserved; throws AaaError(lifecycle_unsupported) at runtime. */
    lifecycle: "one-shot";
    sessionId: string;
    resume?: boolean;
    cwd?: string;
    env?: {
        allowlist: string[];
        extra?: Record<string, string>;
    };
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
    /** Default false; opt out of D6 strict-refuse version check. */
    allowProtocolSkew?: boolean;
    /** Optional MCP servers to forward via `--mcp-servers` (A1). */
    mcpServers?: Record<string, McpServerConfig>;
    /** Optional host envelope forwarded via `--host-capabilities` (A1). */
    host?: {
        capabilities?: HostCapabilities;
    };
    /** Per-submit timeout in ms (default: 10 minutes). */
    timeoutMs?: number;
    /** Replaces the real resolveBinaryPath() call. */
    _binaryResolver?: () => string;
}
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
export declare function spawnAgent(params: SpawnAgentParams): Promise<SessionHandle>;
