/**
 * amplifier-agent-client-ts — public entry point.
 *
 * Exports the locked public API from design §8.2, narrowed to Mode A v2
 * (amendment §5). `spawnAgent` is synchronous-in-spirit: it validates
 * parameters, resolves the engine binary path, builds the subprocess
 * environment, and constructs a `SessionHandle`. **No subprocess is spawned
 * at spawn-time** — the engine is launched per `submit()` (amendment §5.2).
 */
export { AaaError, SessionHandle, DEFAULT_TIMEOUT_MS } from "./session.js";
export type { DisplayEvent, EngineInfo, SessionHandleParams, } from "./session.js";
export type { ApprovalResponse } from "./approval.js";
export type { EngineVersionPayload } from "./spawn.js";
/** @public */
export { assembleArgv } from "./argv-builder.js";
/** @public */
export type { AssembleArgvInput } from "./argv-builder.js";
/** @public */
export { listModels, ListModelsError } from "./list-models.js";
/** @public */
export type { ListModelsParams, ModelInfo, ModelsListEnvelope, } from "./list-models.js";
/** @public */
export { resolveMcpConfigPath, cleanupSpillFile } from "./mcp-spill.js";
/** @public */
export type { McpSpillResult } from "./mcp-spill.js";
/** @public */
export { resolveBinaryPath, buildEnv, probeEngineVersion, DEFAULT_ALLOWLIST, BLOCKED_ENV_KEYS, } from "./spawn.js";
/** @public */
export type { ResolveBinaryPathOptions, BuildEnvOptions, } from "./spawn.js";
/** @public */
export { Transport, parseNdjsonStream } from "./transport.js";
/** @public */
export type { TransportOptions, ExitInfo, ParseNdjsonStreamOptions, } from "./transport.js";
/** @public */
export { checkProtocolVersion } from "./version.js";
/** @public */
export type { VersionCheckResult, VersionCheckOk, VersionCheckFail, CheckProtocolVersionOptions, } from "./version.js";
/** @public */
export { parseRunOutput, STDERR_TAIL_BYTES } from "./run-output-parser.js";
/** @public */
export type { SubprocessOutcome } from "./run-output-parser.js";
/** @public */
export { makeApprovalHandler } from "./approval.js";
/** @public */
export type { ApprovalAdapter, ApprovalRequest, ApprovalHandler, } from "./approval.js";
import { SessionHandle } from "./session.js";
import type { DisplayEvent, ChildProcessFactory } from "./session.js";
import type { ApprovalResponse } from "./approval.js";
import type { McpServerConfig } from "./types.js";
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
export declare const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0";
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
     * under `~/.local/state/amplifier-agent/workspaces/<workspace>/sessions/<id>/`.
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
     * `AMPLIFIER_AGENT_CONFIG`, then `~/.config/amplifier-agent/host_config.json`).
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
