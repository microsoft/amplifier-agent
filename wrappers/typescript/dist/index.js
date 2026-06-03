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
// Internal imports used by spawnAgent().
import { AaaError, SessionHandle } from "./session.js";
import { resolveBinaryPath, buildEnv, DEFAULT_ALLOWLIST } from "./spawn.js";
/**
 * The protocol version that this TypeScript wrapper requires.
 * Forwarded to the engine via `--protocol-version` on every `submit()`.
 */
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.2.0";
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
export async function spawnAgent(params) {
    // SC-C: reject mid-turn approval callback before any other work. The Mode A
    // wire has no mid-turn request channel; warning-only acceptance would ship
    // silent auto-allow to a host author who believed their callback was wired.
    if (params.approval?.onRequest !== undefined) {
        throw new AaaError("approval_not_supported_in_v1", "Mid-turn approval callbacks (params.approval.onRequest) are not supported in v1. " +
            "The Mode A wire has no mid-turn request channel. The bundle's hooks-approval mount " +
            "is the v1 policy point — auto-approve by default, configurable per-tool via the " +
            "bundle's hooks-approval default-mode and gating settings. To customize approval " +
            "policy in v1, configure the bundle; do not pass an onRequest callback. " +
            "Mid-turn callbacks will return in v1.x — track WG-4 in amendment §6.", { classification: "protocol", severity: "error" });
    }
    // 1. Lifecycle guard (D10).
    if (params.lifecycle !== "one-shot") {
        throw new AaaError("lifecycle_unsupported", `lifecycle '${String(params.lifecycle)}' is not supported in v1; ` +
            `only 'one-shot' is supported. 'burst' is reserved for a future minor version.`);
    }
    // 2. Resolve binary path.
    let binaryPath;
    if (params._binaryResolver) {
        binaryPath = params._binaryResolver();
    }
    else {
        try {
            binaryPath = resolveBinaryPath({
                env: process.env,
            });
        }
        catch (e) {
            const msg = e.message ?? "binary not found";
            throw new AaaError("binary_not_found", msg);
        }
    }
    // 3. Build subprocess environment.
    const allowlist = params.env?.allowlist ?? DEFAULT_ALLOWLIST;
    const extra = params.env?.extra ?? {};
    const subprocessEnv = buildEnv({
        processEnv: process.env,
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
