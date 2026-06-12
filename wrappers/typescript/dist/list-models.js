/**
 * list-models.ts — wrapper-side discovery of provider models.
 *
 * Spawns the Python `amplifier-agent models list … --output json` subcommand
 * and returns the parsed JSON envelope. The Python implementation lives at
 * `src/amplifier_agent_cli/admin/models.py`. This is the discovery half of
 * the model-management story; the override half lives in
 * host_config.provider.config (default_model, effort, temperature, …) which
 * the engine consumes when the wrapper passes `configPath` to `assembleArgv`.
 * The previous per-call `modelOverride` / `effortOverride` fields were
 * removed when host_config became the single source of truth.
 *
 * Two functions are exported:
 *
 *   listModels({ provider, … })
 *     Single-provider discovery (existing). Spawns `models list --provider <p>
 *     --output json`. Returns `ModelsListEnvelope`.
 *
 *   listAllModels({ … })
 *     Aggregate discovery (new in this revision). Spawns `models list
 *     --output json` (no --provider). The engine enumerates every known
 *     provider in parallel and emits a per-provider results envelope so
 *     callers can see at a glance which providers are configured and what
 *     models each reports. Returns `ListAllModelsEnvelope`.
 *
 * Wire contract (kept in sync with the Python emitter):
 *   stdout (exit 0): JSON envelope with schema_version === 1
 *   exit 0 + empty models: legitimate (azure-openai always; ollama when down)
 *   exit 1: usage error (unknown provider) — message on stderr
 *   exit 2: provider error (auth, network, timeout) — message on stderr
 */
import { spawn } from "node:child_process";
/**
 * Error thrown when `listModels()` or `listAllModels()` fails. Carries exit
 * code and stderr so callers can disambiguate auth vs network vs usage errors.
 */
export class ListModelsError extends Error {
    exitCode;
    stderr;
    constructor(message, exitCode, stderr) {
        super(message);
        this.exitCode = exitCode;
        this.stderr = stderr;
        this.name = "ListModelsError";
    }
}
const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_BINARY = "amplifier-agent";
/** Sanity cap on stdout/stderr collection — paranoia against runaway output. */
const MAX_BUFFER_BYTES = 10 * 1024 * 1024;
/**
 * Shared subprocess driver for `models list` invocations.
 *
 * Handles spawn failures, the 15s default timeout, stdout/stderr accumulation
 * with a 10MB sanity cap, and SIGTERM-on-hang. Returns the raw outcome so
 * each public function can apply its own envelope validation and exit-code
 * interpretation.
 */
function runModelsListSubprocess(args, options) {
    return new Promise((resolve, reject) => {
        let stdoutBytes = 0;
        let stderrBytes = 0;
        let stdoutTruncated = false;
        let stderrTruncated = false;
        const stdoutChunks = [];
        const stderrChunks = [];
        let settled = false;
        // Build spawn options — `env: undefined` makes child inherit process.env;
        // an explicit env REPLACES (not merges) so callers stay in control.
        const spawnOptions = {};
        if (options.env !== undefined) {
            spawnOptions.env = options.env;
        }
        else {
            spawnOptions.env = process.env;
        }
        let child;
        try {
            child = spawn(options.binary, args, spawnOptions);
        }
        catch (err) {
            // Synchronous spawn failures (extremely rare — most surface via "error").
            reject(new ListModelsError(`failed to spawn ${options.binary}: ${err instanceof Error ? err.message : String(err)}`, null, ""));
            return;
        }
        const settle = (fn) => {
            if (settled)
                return;
            settled = true;
            clearTimeout(timer);
            fn();
        };
        const timer = setTimeout(() => {
            // Subprocess wedged. Kill it and reject. The "exit" handler may still
            // fire after kill — the `settled` flag suppresses the double-callback.
            try {
                child.kill("SIGTERM");
            }
            catch {
                /* swallow — kill on dead pid throws on some platforms */
            }
            settle(() => reject(new ListModelsError(`listModels timed out after ${options.timeoutMs}ms`, null, decodeBuffer(stderrChunks))));
        }, options.timeoutMs);
        child.stdout?.on("data", (chunk) => {
            if (stdoutTruncated)
                return;
            stdoutBytes += chunk.length;
            if (stdoutBytes > MAX_BUFFER_BYTES) {
                stdoutTruncated = true;
                return;
            }
            stdoutChunks.push(chunk);
        });
        child.stderr?.on("data", (chunk) => {
            if (stderrTruncated)
                return;
            stderrBytes += chunk.length;
            if (stderrBytes > MAX_BUFFER_BYTES) {
                stderrTruncated = true;
                return;
            }
            stderrChunks.push(chunk);
        });
        child.on("error", (err) => {
            // Spawn failure (ENOENT etc.) typically arrives here, not via throw.
            settle(() => reject(new ListModelsError(`failed to spawn ${options.binary}: ${err.message}`, null, decodeBuffer(stderrChunks))));
        });
        child.on("exit", (code) => {
            const stdout = decodeBuffer(stdoutChunks);
            let stderr = decodeBuffer(stderrChunks);
            if (stdoutTruncated)
                stderr += "\n[listModels: stdout truncated at 10MB]";
            if (stderrTruncated)
                stderr += "\n[listModels: stderr truncated at 10MB]";
            settle(() => resolve({ exitCode: code, stdout, stderr }));
        });
    });
}
/**
 * Map a subprocess outcome into a typed envelope or a `ListModelsError`.
 *
 * Both `listModels` and `listAllModels` share the same exit-code contract
 * (0 = ok + envelope on stdout; 1 = usage error; 2 = provider error). They
 * differ only in their stdout shape, which `validate` handles.
 */
function interpretOutcome(outcome, validate) {
    const { exitCode, stdout, stderr } = outcome;
    if (exitCode === 0) {
        let parsed;
        try {
            parsed = JSON.parse(stdout);
        }
        catch (err) {
            throw new ListModelsError(`invalid envelope: JSON parse failed (${err instanceof Error ? err.message : String(err)})`, exitCode, stderr);
        }
        const validation = validate(parsed);
        if (validation.ok) {
            return validation.value;
        }
        throw new ListModelsError(`invalid envelope: ${validation.reason}`, exitCode, stderr);
    }
    if (exitCode === 1) {
        throw new ListModelsError(`usage error: ${stderr.trim()}`, exitCode, stderr);
    }
    if (exitCode === 2) {
        throw new ListModelsError(`provider error: ${stderr.trim()}`, exitCode, stderr);
    }
    throw new ListModelsError(`subprocess exited with unexpected code`, exitCode, stderr);
}
/**
 * Spawn `amplifier-agent models list --provider <p> --output json` and return
 * the parsed envelope. See {@link ListModelsError} for failure modes.
 *
 * An empty `models: []` is NOT an error — azure-openai always returns this,
 * ollama returns it when the daemon is down. Callers should treat it as a
 * legitimate "no models discoverable" result, not a failure.
 */
export async function listModels(params) {
    const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const binary = params.binaryPath ?? DEFAULT_BINARY;
    const args = ["models", "list", "--provider", params.provider, "--output", "json"];
    const outcome = await runModelsListSubprocess(args, { timeoutMs, binary, env: params.env });
    return interpretOutcome(outcome, validateSingleEnvelope);
}
/**
 * Spawn `amplifier-agent models list --output json` (no --provider) and
 * return the parsed aggregate envelope. The engine queries every known
 * provider in parallel and returns one `ProviderListResult` per provider
 * in {@link ListAllModelsEnvelope.results}.
 *
 * Per-provider failure modes (auth missing, module not installed, network
 * error) are reported on individual `results` entries with `status !== "ok"`
 * — this function only rejects on subprocess-level failures (spawn error,
 * exit code 1 = usage, exit code 2 = engine-wide provider error, timeout).
 *
 * Recommended for hosts that want a single "all available models" dropdown
 * without orchestrating four sequential single-provider calls. One spawn,
 * engine-side parallelism, native per-provider auth status.
 */
export async function listAllModels(params = {}) {
    const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const binary = params.binaryPath ?? DEFAULT_BINARY;
    const args = ["models", "list", "--output", "json"];
    const outcome = await runModelsListSubprocess(args, { timeoutMs, binary, env: params.env });
    return interpretOutcome(outcome, validateAggregateEnvelope);
}
function decodeBuffer(chunks) {
    if (chunks.length === 0)
        return "";
    return Buffer.concat(chunks).toString("utf8");
}
/**
 * Lenient single-provider envelope check: enough to catch malformed payloads,
 * not a full pydantic-style schema validation. We accept extra fields and
 * let the caller see them as `unknown` via TS structural typing.
 */
function validateSingleEnvelope(value) {
    if (value === null || typeof value !== "object") {
        return { ok: false, reason: "envelope is not an object" };
    }
    const obj = value;
    if (obj["schema_version"] !== 1) {
        return {
            ok: false,
            reason: `unsupported schema_version (expected 1, got ${JSON.stringify(obj["schema_version"])})`,
        };
    }
    if (typeof obj["provider"] !== "string") {
        return { ok: false, reason: "provider must be a string" };
    }
    if (!Array.isArray(obj["models"])) {
        return { ok: false, reason: "models must be an array" };
    }
    for (let i = 0; i < obj["models"].length; i++) {
        const m = obj["models"][i];
        if (m === null || typeof m !== "object") {
            return { ok: false, reason: `models[${i}] is not an object` };
        }
        if (typeof m["id"] !== "string") {
            return { ok: false, reason: `models[${i}].id must be a string` };
        }
    }
    // fetched_at is documented as ISO 8601 but we don't enforce — the Python
    // side guarantees it, and a stricter parse here would just be ceremony.
    return { ok: true, value: obj };
}
/**
 * Lenient aggregate envelope check. Same posture as `validateSingleEnvelope`:
 * verify the structural skeleton, treat unknown fields as forward-compat
 * extension points, and let pathological payloads surface as parse errors.
 */
function validateAggregateEnvelope(value) {
    if (value === null || typeof value !== "object") {
        return { ok: false, reason: "envelope is not an object" };
    }
    const obj = value;
    if (obj["schema_version"] !== 1) {
        return {
            ok: false,
            reason: `unsupported schema_version (expected 1, got ${JSON.stringify(obj["schema_version"])})`,
        };
    }
    if (!Array.isArray(obj["results"])) {
        return { ok: false, reason: "results must be an array" };
    }
    for (let i = 0; i < obj["results"].length; i++) {
        const r = obj["results"][i];
        if (r === null || typeof r !== "object") {
            return { ok: false, reason: `results[${i}] is not an object` };
        }
        const result = r;
        if (typeof result["provider"] !== "string") {
            return { ok: false, reason: `results[${i}].provider must be a string` };
        }
        if (typeof result["status"] !== "string") {
            return { ok: false, reason: `results[${i}].status must be a string` };
        }
        if (!Array.isArray(result["models"])) {
            return { ok: false, reason: `results[${i}].models must be an array` };
        }
        for (let j = 0; j < result["models"].length; j++) {
            const m = result["models"][j];
            if (m === null || typeof m !== "object") {
                return { ok: false, reason: `results[${i}].models[${j}] is not an object` };
            }
            if (typeof m["id"] !== "string") {
                return { ok: false, reason: `results[${i}].models[${j}].id must be a string` };
            }
        }
        // `error` is optional and only meaningful when status !== "ok"; don't
        // enforce its presence or shape here. Hosts that care can switch on
        // status and read `error` lenient-string-style.
    }
    return { ok: true, value: obj };
}
