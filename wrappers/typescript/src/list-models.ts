/**
 * list-models.ts — wrapper-side discovery of provider models.
 *
 * Spawns the Python `amplifier-agent models list --provider <p> --output json`
 * subcommand and returns the parsed JSON envelope. The Python implementation
 * lives at `src/amplifier_agent_cli/admin/models.py`. This module is the TS
 * counterpart to the override path that `argv-builder.ts` exposes via
 * `modelOverride` / `effortOverride` — together they let TS consumers both
 * discover available models and pin one for a run.
 *
 * Wire contract (kept in sync with the Python emitter):
 *   stdout (exit 0): JSON envelope with schema_version === 1
 *   exit 0 + empty models: legitimate (azure-openai always; ollama when down)
 *   exit 1: usage error (unknown provider) — message on stderr
 *   exit 2: provider error (auth, network, timeout) — message on stderr
 */

import { spawn } from "node:child_process";

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
export class ListModelsError extends Error {
  constructor(
    message: string,
    public readonly exitCode: number | null,
    public readonly stderr: string,
  ) {
    super(message);
    this.name = "ListModelsError";
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_BINARY = "amplifier-agent";
/** Sanity cap on stdout/stderr collection — paranoia against runaway output. */
const MAX_BUFFER_BYTES = 10 * 1024 * 1024;

/**
 * Spawn `amplifier-agent models list --provider <p> --output json` and return
 * the parsed envelope. See {@link ListModelsError} for failure modes.
 *
 * An empty `models: []` is NOT an error — azure-openai always returns this,
 * ollama returns it when the daemon is down. Callers should treat it as a
 * legitimate "no models discoverable" result, not a failure.
 */
export async function listModels(params: ListModelsParams): Promise<ModelsListEnvelope> {
  const timeoutMs = params.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const binary = params.binaryPath ?? DEFAULT_BINARY;
  const args = ["models", "list", "--provider", params.provider, "--output", "json"];

  // If caller supplied an env, use it as-is. Otherwise inherit process.env.
  // Never silently strip — callers may need provider API keys.
  const spawnOptions: { env?: NodeJS.ProcessEnv } = {};
  if (params.env !== undefined) {
    spawnOptions.env = params.env;
  } else {
    spawnOptions.env = process.env;
  }

  return new Promise<ModelsListEnvelope>((resolve, reject) => {
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let stdoutTruncated = false;
    let stderrTruncated = false;
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    let settled = false;

    let child;
    try {
      child = spawn(binary, args, spawnOptions);
    } catch (err) {
      // Synchronous spawn failures (extremely rare — most surface via "error").
      reject(
        new ListModelsError(
          `failed to spawn ${binary}: ${err instanceof Error ? err.message : String(err)}`,
          null,
          "",
        ),
      );
      return;
    }

    const settle = (fn: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn();
    };

    const timer = setTimeout(() => {
      // Subprocess wedged. Kill it and reject. The "exit" handler may still
      // fire after kill — the `settled` flag suppresses the double-callback.
      try {
        child.kill("SIGTERM");
      } catch {
        /* swallow — kill on dead pid throws on some platforms */
      }
      settle(() =>
        reject(
          new ListModelsError(
            `listModels timed out after ${timeoutMs}ms`,
            null,
            decodeBuffer(stderrChunks),
          ),
        ),
      );
    }, timeoutMs);

    child.stdout?.on("data", (chunk: Buffer) => {
      if (stdoutTruncated) return;
      stdoutBytes += chunk.length;
      if (stdoutBytes > MAX_BUFFER_BYTES) {
        stdoutTruncated = true;
        return;
      }
      stdoutChunks.push(chunk);
    });

    child.stderr?.on("data", (chunk: Buffer) => {
      if (stderrTruncated) return;
      stderrBytes += chunk.length;
      if (stderrBytes > MAX_BUFFER_BYTES) {
        stderrTruncated = true;
        return;
      }
      stderrChunks.push(chunk);
    });

    child.on("error", (err: Error) => {
      // Spawn failure (ENOENT etc.) typically arrives here, not via throw.
      settle(() =>
        reject(
          new ListModelsError(
            `failed to spawn ${binary}: ${err.message}`,
            null,
            decodeBuffer(stderrChunks),
          ),
        ),
      );
    });

    child.on("exit", (code: number | null) => {
      const stdout = decodeBuffer(stdoutChunks);
      let stderr = decodeBuffer(stderrChunks);
      if (stdoutTruncated) stderr += "\n[listModels: stdout truncated at 10MB]";
      if (stderrTruncated) stderr += "\n[listModels: stderr truncated at 10MB]";

      if (code === 0) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(stdout);
        } catch (err) {
          settle(() =>
            reject(
              new ListModelsError(
                `invalid envelope: JSON parse failed (${err instanceof Error ? err.message : String(err)})`,
                code,
                stderr,
              ),
            ),
          );
          return;
        }
        const validation = validateEnvelope(parsed);
        if (validation.ok) {
          settle(() => resolve(validation.value));
        } else {
          settle(() =>
            reject(new ListModelsError(`invalid envelope: ${validation.reason}`, code, stderr)),
          );
        }
        return;
      }

      if (code === 1) {
        settle(() =>
          reject(new ListModelsError(`usage error: ${stderr.trim()}`, code, stderr)),
        );
        return;
      }
      if (code === 2) {
        settle(() =>
          reject(new ListModelsError(`provider error: ${stderr.trim()}`, code, stderr)),
        );
        return;
      }

      settle(() =>
        reject(
          new ListModelsError(`subprocess exited with unexpected code`, code, stderr),
        ),
      );
    });
  });
}

function decodeBuffer(chunks: Buffer[]): string {
  if (chunks.length === 0) return "";
  return Buffer.concat(chunks).toString("utf8");
}

type ValidationResult =
  | { ok: true; value: ModelsListEnvelope }
  | { ok: false; reason: string };

/**
 * Lenient envelope check: enough to catch malformed payloads, not a full
 * pydantic-style schema validation. We accept extra fields and let the
 * caller see them as `unknown` via TS structural typing.
 */
function validateEnvelope(value: unknown): ValidationResult {
  if (value === null || typeof value !== "object") {
    return { ok: false, reason: "envelope is not an object" };
  }
  const obj = value as Record<string, unknown>;
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
    if (typeof (m as Record<string, unknown>)["id"] !== "string") {
      return { ok: false, reason: `models[${i}].id must be a string` };
    }
  }
  // fetched_at is documented as ISO 8601 but we don't enforce — the Python
  // side guarantees it, and a stricter parse here would just be ceremony.
  return { ok: true, value: obj as unknown as ModelsListEnvelope };
}
