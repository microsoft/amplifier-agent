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
export declare const PROMPT_SPILL_THRESHOLD_BYTES = 16384;
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
export declare function resolvePromptFilePath(prompt: string, sessionId: string): Promise<PromptSpillResult>;
/**
 * Idempotently remove a prompt spill file. Safe to call with `null` (no-op)
 * and safe to call when the file is already gone (ENOENT swallowed). Other
 * I/O errors propagate.
 */
export declare function cleanupPromptSpillFile(promptFile: string | null | undefined): Promise<void>;
