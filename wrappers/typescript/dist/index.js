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
export { resolveMcpConfigPath, cleanupSpillFile } from "./mcp-spill.js";
/** @public */
export { resolveBinaryPath, buildEnv, probeEngineVersion, DEFAULT_ALLOWLIST, BLOCKED_ENV_KEYS, } from "./spawn.js";
/** @public */
export { Transport, parseNdjsonStream } from "./transport.js";
/** @public */
export { checkProtocolVersion } from "./version.js";
/** @public */
export { parseRunOutput, STDERR_TAIL_BYTES } from "./run-output-parser.js";
/** @public */
export { makeApprovalHandler } from "./approval.js";
// Internal imports used by spawnAgent().
import { AaaError, SessionHandle } from "./session.js";
import { resolveBinaryPath, buildEnv, probeEngineVersion, DEFAULT_ALLOWLIST, } from "./spawn.js";
import { checkProtocolVersion } from "./version.js";
/**
 * The protocol version that this TypeScript wrapper requires.
 * Forwarded to the engine via `--protocol-version` on every `submit()` and
 * checked at `spawnAgent()` time against the engine's reported protocol
 * version (see Issue #9 — `checkProtocolVersion()` is wired into the init
 * path so skew fails fast wrapper-side before any subprocess spawn).
 */
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0";
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
    // Issue #10: `approval.mode` is the supported policy hook in v1; `onRequest`
    // remains unsupported until a mid-turn channel returns (track WG-4).
    if (params.approval?.onRequest !== undefined) {
        throw new AaaError("approval_not_supported_in_v1", "Mid-turn approval callbacks (params.approval.onRequest) are not supported in v1. " +
            "The Mode A wire has no mid-turn request channel. Use the static-policy shape " +
            "`approval: { mode: 'yes' | 'no' | 'prompt' }` instead — it maps to engine argv " +
            "(`-y` / `-n`) and to `host_config.approval.mode`. Mid-turn callbacks will return " +
            "in v1.x — track WG-4 in amendment §6.", { classification: "protocol", severity: "error" });
    }
    // Issue #10: validate the static-policy shape if present.
    let approvalModeArg;
    if (params.approval?.mode !== undefined) {
        const m = params.approval.mode;
        if (m !== "yes" && m !== "no" && m !== "prompt") {
            throw new AaaError("invalid_approval_mode", `params.approval.mode must be 'yes', 'no', or 'prompt' (got ${JSON.stringify(m)})`, { classification: "protocol", severity: "error" });
        }
        approvalModeArg = m;
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
    let engineVersionPayload;
    try {
        if (params._engineVersionProbe) {
            engineVersionPayload = await params._engineVersionProbe();
        }
        else {
            engineVersionPayload = await probeEngineVersion(binaryPath, subprocessEnv);
        }
    }
    catch (e) {
        // Probe failure is non-fatal when skew is allowed: fall back to empty
        // metadata. Otherwise surface it as a typed error.
        if (params.allowProtocolSkew === true) {
            engineVersionPayload = { version: "", protocolVersion: "" };
        }
        else {
            const msg = e.message ?? "engine version probe failed";
            throw new AaaError("engine_probe_failed", `Could not probe engine binary at ${binaryPath} for protocol version: ${msg}`, { classification: "transport", severity: "error" });
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
        ...(params.runChildProcess !== undefined
            ? { runChildProcess: params.runChildProcess }
            : {}),
        // Issue #1: forward host config path so assembleArgv can emit
        // --config <path>.
        ...(params.configPath !== undefined ? { configPath: params.configPath } : {}),
        // Issue #10: forward the validated approval mode so assembleArgv can
        // emit -y / -n / nothing.
        ...(approvalModeArg !== undefined ? { approvalMode: approvalModeArg } : {}),
        // Issue #4: thread display.onEvent through so SessionHandle can
        // dispatch parsed NDJSON wire events to it.
        ...(params.display !== undefined ? { display: params.display } : {}),
        // Issue #7: persist engine metadata resolved during the probe.
        engineVersion: engineVersionPayload.version,
        bundleDigest: engineVersionPayload.bundleDigest ?? "",
    });
}
