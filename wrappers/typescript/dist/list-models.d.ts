/**
 * list-models.ts — wrapper-side discovery of provider models.
 *
 * Spawns the Python `amplifier-agent models list --provider <p> --output json`
 * subcommand and returns the parsed JSON envelope. The Python implementation
 * lives at `src/amplifier_agent_cli/admin/models.py`. This is the discovery
 * half of the model-management story; the override half lives in
 * host_config.provider.config (default_model, effort, temperature, ...)
 * which the engine consumes when the wrapper passes `configPath` to
 * `assembleArgv`. The previous per-call `modelOverride` / `effortOverride`
 * fields were removed when host_config became the single source of truth.
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
/** JSON envelope returned by `amplifier-agent models list --output json`. */
export interface ModelsListEnvelope {
    schema_version: 1;
    provider: string;
    fetched_at: string;
    models: ModelInfo[];
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
 * Error thrown when `listModels()` fails. Carries exit code and stderr so
 * callers can disambiguate auth vs network vs usage errors.
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
