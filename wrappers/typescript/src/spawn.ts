/**
 * Binary discovery, environment allowlist, and engine version probe.
 *
 * resolveBinaryPath() — find the amplifier-agent binary
 * buildEnv()          — filter subprocess env to a safe allowlist
 * probeEngineVersion() — run `amplifier-agent version --json` and parse result
 */

import { execFile, execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { promisify } from "node:util";

import { AaaError } from "./session.js";

const execFileAsync = promisify(execFile);

/** Variables always passed through to subprocess (exact name match). */
export const DEFAULT_ALLOWLIST: string[] = [
  "PATH",
  "HOME",
  "USER",
  "LANG",
  "TERM",
  "TMPDIR",
];

/**
 * Environment variable names that are NEVER allowed in env.extra
 * regardless of allowlist (design §4.12.1). These can be used to inject
 * code into the subprocess (e.g. via shared-library preloading or Python
 * import-path manipulation).
 */
export const BLOCKED_ENV_KEYS: ReadonlySet<string> = new Set([
  "PYTHONPATH",
  "LD_PRELOAD",
  "LD_LIBRARY_PATH",
  "PYTHONSTARTUP",
  "PATH",
  "PYTHONHOME",
  "PYTHONNOUSERSITE",
  "DYLD_INSERT_LIBRARIES",
  "DYLD_LIBRARY_PATH",
]);

export interface ResolveBinaryPathOptions {
  /** Environment to look up AMPLIFIER_AGENT_BIN from. Defaults to process.env. */
  env?: Record<string, string | undefined>;
}

/**
 * Resolve the path to the amplifier-agent binary.
 *
 * Resolution order:
 * 1. AMPLIFIER_AGENT_BIN env var (if set and the path exists on disk)
 * 2. `which amplifier-agent` via PATH lookup
 *
 * @throws Error with code 'binary_not_found' if neither path resolves.
 */
export function resolveBinaryPath(opts: ResolveBinaryPathOptions = {}): string {
  const env = opts.env ?? (process.env as Record<string, string | undefined>);

  const envBin = env["AMPLIFIER_AGENT_BIN"];
  if (envBin) {
    if (existsSync(envBin)) {
      return envBin;
    }
    // env var set but path doesn't exist — still return it (caller handles errors)
    return envBin;
  }

  try {
    const whichResult = execSync("which amplifier-agent", { encoding: "utf-8" }).trim();
    if (whichResult) {
      return whichResult;
    }
  } catch {
    // which failed — binary not on PATH
  }

  const err = new Error(
    "amplifier-agent binary not found. " +
      "Install amplifier-agent: pip install amplifier-agent, " +
      "or set AMPLIFIER_AGENT_BIN to the binary path.",
  );
  (err as NodeJS.ErrnoException).code = "binary_not_found";
  throw err;
}

export interface BuildEnvOptions {
  /** The caller's process environment (e.g. process.env). */
  processEnv: Record<string, string | undefined>;
  /**
   * List of exact variable names that are allowed through.
   * Variables with AMPLIFIER_ or LC_ prefixes are always included.
   */
  allowlist: string[];
  /** Additional variables merged on top (override any allowlisted value). */
  extra?: Record<string, string>;
}

/**
 * Build the subprocess environment from the caller's process environment.
 *
 * Only variables whose name is in `allowlist`, starts with `AMPLIFIER_`,
 * or starts with `LC_` are included.  `extra` entries are merged last.
 */
export function buildEnv(opts: BuildEnvOptions): Record<string, string> {
  const { processEnv, allowlist, extra = {} } = opts;

  // Reject blocked keys in extra up-front (design §4.12.1, SC-3).
  for (const key of Object.keys(extra)) {
    if (BLOCKED_ENV_KEYS.has(key)) {
      throw new AaaError(
        "env_injection_rejected",
        `env.extra key '${key}' is blocked for security reasons (design §4.12.1).`,
        { classification: "protocol", severity: "error" },
      );
    }
  }

  const allowSet = new Set(allowlist);
  const result: Record<string, string> = {};

  for (const [key, value] of Object.entries(processEnv)) {
    if (value === undefined) continue;
    if (
      allowSet.has(key) ||
      key.startsWith("AMPLIFIER_") ||
      key.startsWith("LC_")
    ) {
      result[key] = value;
    }
  }

  // Merge extras last — they win over everything
  for (const [key, value] of Object.entries(extra)) {
    result[key] = value;
  }

  return result;
}

export interface EngineVersionPayload {
  version: string;
  protocolVersion: string;
  bundleDigest?: string;
}

/**
 * Run `<binPath> version --json` and parse the JSON response.
 *
 * @param binPath   Absolute path to the amplifier-agent binary.
 * @param env       Environment to pass to the subprocess.
 * @param timeoutMs Timeout in milliseconds (default: 5000).
 */
export async function probeEngineVersion(
  binPath: string,
  env: Record<string, string>,
  timeoutMs = 5000,
): Promise<EngineVersionPayload> {
  const { stdout } = await execFileAsync(binPath, ["version", "--json"], {
    encoding: "utf-8",
    timeout: timeoutMs,
    env,
  });
  return JSON.parse(stdout.trim()) as EngineVersionPayload;
}
