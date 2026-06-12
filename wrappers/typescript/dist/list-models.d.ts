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
/** Single model entry — mirrors amplifier-core ModelInfo.model_dump(). */
export interface ModelInfo {
    id: string;
    display_name: string;
    context_window: number;
    max_output_tokens: number;
    capabilities: string[];
    defaults: Record<string, unknown>;
}
/** JSON envelope returned by `amplifier-agent models list --provider <p> --output json`. */
export interface ModelsListEnvelope {
    schema_version: 1;
    provider: string;
    fetched_at: string;
    models: ModelInfo[];
}
/**
 * Per-provider result inside the aggregate envelope.
 *
 * The `status` field is kept as a free-form string rather than a union so the
 * wrapper stays forward-compatible with new statuses the engine may add.
 * Current values emitted by the engine (see `admin/models.py`):
 *
 *   - "ok"                    — provider returned a model list (possibly empty)
 *   - "credentials_missing"   — provider module loaded but auth not configured
 *   - "module_not_installed"  — provider module not present in the bundle
 *   - "error"                 — provider raised an error during discovery
 *
 * When `status !== "ok"`, `models` is `[]` and an `error` field carries the
 * human-readable reason. Callers should compare against the literal "ok"
 * rather than enumerating failure cases — that way new failure modes don't
 * silently get treated as success.
 */
export interface ProviderListResult {
    provider: string;
    status: string;
    models: ModelInfo[];
    error?: string;
}
/** JSON envelope returned by `amplifier-agent models list --output json` (aggregate mode). */
export interface ListAllModelsEnvelope {
    schema_version: 1;
    fetched_at: string;
    results: ProviderListResult[];
}
export interface ListModelsParams {
    /** Provider name (anthropic, openai, ollama, azure-openai). */
    provider: string;
    /** Subprocess timeout in milliseconds. Default: 15000. */
    timeoutMs?: number;
    /**
     * Path to amplifier-agent binary or executable name on PATH.
     * Default: "amplifier-agent".
     */
    binaryPath?: string;
    /**
     * Environment variables passed to the subprocess. If undefined, inherits
     * process.env. Use this to forward provider API keys (ANTHROPIC_API_KEY etc.).
     */
    env?: NodeJS.ProcessEnv;
}
/**
 * Parameters for {@link listAllModels}. Same shape as {@link ListModelsParams}
 * minus the `provider` field — aggregate mode queries every known provider.
 */
export interface ListAllModelsParams {
    /** Subprocess timeout in milliseconds. Default: 15000. */
    timeoutMs?: number;
    /**
     * Path to amplifier-agent binary or executable name on PATH.
     * Default: "amplifier-agent".
     */
    binaryPath?: string;
    /**
     * Environment variables passed to the subprocess. If undefined, inherits
     * process.env. Use this to forward provider API keys (ANTHROPIC_API_KEY,
     * OPENAI_API_KEY, etc.) — providers without their keys will return
     * `status: "credentials_missing"` in the aggregate envelope, not raise.
     */
    env?: NodeJS.ProcessEnv;
}
/**
 * Error thrown when `listModels()` or `listAllModels()` fails. Carries exit
 * code and stderr so callers can disambiguate auth vs network vs usage errors.
 */
export declare class ListModelsError extends Error {
    readonly exitCode: number | null;
    readonly stderr: string;
    constructor(message: string, exitCode: number | null, stderr: string);
}
/**
 * Spawn `amplifier-agent models list --provider <p> --output json` and return
 * the parsed envelope. See {@link ListModelsError} for failure modes.
 *
 * An empty `models: []` is NOT an error — azure-openai always returns this,
 * ollama returns it when the daemon is down. Callers should treat it as a
 * legitimate "no models discoverable" result, not a failure.
 */
export declare function listModels(params: ListModelsParams): Promise<ModelsListEnvelope>;
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
export declare function listAllModels(params?: ListAllModelsParams): Promise<ListAllModelsEnvelope>;
