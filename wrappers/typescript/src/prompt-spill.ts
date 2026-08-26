/**
 * prompt-spill.ts — oversized-prompt spill-to-file resolution.
 *
 * Companion to `mcp-spill.ts` and structured identically. A large prompt
 * cannot ride on argv: it travels as the final positional element, and a
 * single argv element is capped at `MAX_ARG_STRLEN` (131072 bytes) on Linux
 * while the whole command line is capped at 32767 chars on Windows. Past
 * those ceilings the spawn fails with `E2BIG` before the engine even boots.
 * So the wrapper spills the prompt to a 0600 tmpfile under
 * `${XDG_RUNTIME_DIR || os.tmpdir()}/amplifier-agent/<sessionId>/prompt.txt`
 * and passes `--prompt-file <path>` instead of the positional argument.
 *
 * This mirrors the treatment the MCP server map already gets — see
 * docs/spec/wrapper-contract.md, "Prompt spill".
 *
 * `cleanupPromptSpillFile` is the matching teardown — idempotent unlink that
 * swallows ENOENT so callers can call it unconditionally on every exit path.
 */
import { mkdir, writeFile, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Spill any prompt whose UTF-8 encoded length reaches this many bytes.
 *
 * The threshold must sit safely under the SMALLEST platform ceiling. That is
 * NOT Linux's 131072-byte per-element cap — it is Windows' 32767-CHARACTER cap
 * on the ENTIRE command line, which the prompt shares with the binary path and
 * every other flag (`--session-id`, `--config`, `--cwd`, `--workspace`, ...).
 * 16384 leaves roughly half of that budget as headroom for the rest of the
 * argv, and still keeps ordinary prompts on the fast in-memory path.
 *
 * The comparison is made against the UTF-8 ENCODED byte length
 * (`Buffer.byteLength(prompt, "utf8")`), never `prompt.length`: a multibyte
 * prompt occupies more bytes on the wire than it has characters, and the OS
 * limit is denominated in bytes.
 */
export const PROMPT_SPILL_THRESHOLD_BYTES = 16384;

/**
 * Result of deciding whether to spill the prompt to a tmpfile.
 *
 * - When the prompt fits on argv: `promptFile` is `null` and the caller passes
 *   the prompt positionally as before.
 * - Otherwise: `promptFile` points at the 0600 spill file containing the
 *   prompt text verbatim, and the caller emits `--prompt-file <path>` with no
 *   positional prompt.
 */
export interface PromptSpillResult {
  promptFile: string | null;
}

/**
 * Compute the base directory for spill files. Prefers
 * `$XDG_RUNTIME_DIR/amplifier-agent` (typically a tmpfs on Linux) and falls
 * back to `os.tmpdir()/amplifier-agent` otherwise.
 */
function spillBaseDir(): string {
  const xdg = process.env["XDG_RUNTIME_DIR"];
  if (xdg && xdg.length > 0) {
    return join(xdg, "amplifier-agent");
  }
  return join(tmpdir(), "amplifier-agent");
}

/**
 * Resolve the prompt file path to pass as `--prompt-file`.
 *
 * Spills to a 0600 tmpfile under a 0700 per-session dir when the prompt's
 * UTF-8 byte length reaches `PROMPT_SPILL_THRESHOLD_BYTES`; otherwise does no
 * I/O at all and reports `promptFile: null`.
 *
 * The write completes — and the file handle is closed — before this promise
 * resolves, which matters on Windows, where a child process cannot open a file
 * the parent still holds. The content is the prompt text verbatim: UTF-8, with
 * no newline translation, because the engine reads the file as strict `utf-8`
 * and any `\n` → `\r\n` rewrite would corrupt the caller's prompt in transit.
 *
 * @param prompt    The caller's prompt text.
 * @param sessionId Session identifier; used as the per-session subdirectory
 *                  under the spill base so concurrent sessions never clash.
 *
 * @returns A `PromptSpillResult` with the on-disk prompt path (or null when
 *          the prompt is small enough to travel as a positional argv element).
 */
export async function resolvePromptFilePath(
  prompt: string,
  sessionId: string,
): Promise<PromptSpillResult> {
  if (Buffer.byteLength(prompt, "utf8") < PROMPT_SPILL_THRESHOLD_BYTES) {
    return { promptFile: null };
  }

  const dir = join(spillBaseDir(), sessionId);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const filePath = join(dir, "prompt.txt");
  // The prompt is caller data and may carry secrets, so the file is created
  // 0600. `writeFile` opens, writes and closes before the promise resolves.
  await writeFile(filePath, prompt, { encoding: "utf8", mode: 0o600 });

  return { promptFile: filePath };
}

/**
 * Idempotently remove a prompt spill file. Safe to call with `null` (no-op)
 * and safe to call when the file is already gone (ENOENT swallowed). Other
 * I/O errors propagate.
 */
export async function cleanupPromptSpillFile(
  promptFile: string | null | undefined,
): Promise<void> {
  if (!promptFile) return;
  try {
    await unlink(promptFile);
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return;
    throw err;
  }
}
