/**
 * run-output-parser.ts — parse the Mode A v2 subprocess outcome into a DisplayEvent.
 *
 * Implements §4.1 envelope schema and §4.4 (SC-D) precedence rules from
 * `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md`:
 *
 *   Rule 1 — envelope parseable per §4.1 → envelope is authoritative.
 *     The `error` field (null or populated) drives the wrapper's outcome.
 *     The exit code is informational and does NOT override the envelope.
 *
 *   Rule 2 — envelope absent / unparseable / partial → synthesize an error
 *            event from exit code and stderr tail. Partial JSON is NOT
 *            half-parsed (belt-and-suspenders): if any required §4.1 field
 *            is missing, the envelope is treated as unparseable. The turn id
 *            is unknowable here (the engine assigns it), but the SESSION id is
 *            the caller's own — pass it as `fallbackSessionId` and it is
 *            reported rather than dropped.
 *
 * On the Rule 1 path the envelope is now *read*, not merely shape-checked:
 * `sessionId`, `turnId` and the `metadata` usage block are surfaced on the
 * terminal event. The wrapper performs no arithmetic of its own — the engine's
 * UsageAccumulator already summed the turn, so re-summing here would
 * double-count.
 *
 * `stderrTail` is bounded by `stderrTailBytes` in real UTF-8 BYTES (see
 * `tailStderrBytes`); `STDERR_TAIL_BYTES` (4096) is the default.
 */
import type { DisplayEvent } from "./session.js";
/** Default cap on `stderrTail`, in BYTES of UTF-8. */
export declare const STDERR_TAIL_BYTES = 4096;
/** Outcome of running the `amplifier-agent run --output json` subprocess. */
export interface SubprocessOutcome {
    stdout: string;
    stderr: string;
    exitCode: number;
}
/** Options for `parseRunOutput`. */
export interface ParseRunOutputOptions {
    /**
     * Byte cap for `stderrTail` on the returned event.
     *
     * - a positive number — at most that many UTF-8 BYTES, taken from the end.
     * - `null`            — the ENTIRE stderr buffer, uncapped.
     * - `0`               — capture disabled; `stderrTail` is omitted.
     * - `undefined`       — not supplied; `STDERR_TAIL_BYTES` (4096) applies.
     *
     * The cap applies to whatever ends up in `stderrTail`, including a tail the
     * engine supplied inside the envelope, so `0` really does mean "do not give
     * me stderr".
     */
    stderrTailBytes?: number | null;
    /**
     * The caller's own session id, reported on the SYNTHESIZED (Rule 2) events
     * only. Never overrides the envelope: on the Rule 1 path the envelope's
     * `sessionId` is authoritative and this option is ignored.
     *
     * `undefined` (the default) preserves the previous behaviour of omitting the
     * field — a caller with no session id to offer, such as a host post-parsing
     * a captured payload, is not made to invent one.
     */
    fallbackSessionId?: string;
}
/**
 * Return the last `limit` BYTES of `text`, never splitting a codepoint.
 *
 * The cap is expressed in bytes because that is what a host budgeting a log
 * line or a payload actually cares about; a character count is meaningless for
 * that purpose the moment stderr contains non-ASCII. JavaScript strings are
 * UTF-16 code units, so `String.prototype.slice` cannot express this — the
 * work happens on a `Buffer`.
 *
 * `limit` semantics: a positive number caps to that many UTF-8 bytes, `null`
 * means the whole buffer, `undefined` falls back to `STDERR_TAIL_BYTES`, and
 * `0` (or negative) disables capture and returns `undefined`. An empty `text`
 * always returns `undefined`: there is nothing to report.
 *
 * Boundary safety: after slicing the encoded buffer, leading UTF-8
 * continuation bytes (`0b10xxxxxx`) are dropped so the slice begins on a lead
 * byte. The result therefore decodes cleanly — no U+FFFD — at the cost of up
 * to 3 bytes fewer than `limit`. Returning slightly less than the cap is
 * correct; returning a broken character is not.
 *
 * @public
 */
export declare function tailStderrBytes(text: string, limit?: number | null | undefined): string | undefined;
/**
 * Parse a subprocess outcome into a single DisplayEvent.
 *
 * See module docstring for precedence rules.
 */
export declare function parseRunOutput(outcome: SubprocessOutcome, options?: ParseRunOutputOptions): DisplayEvent;
