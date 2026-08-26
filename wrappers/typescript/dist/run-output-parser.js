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
/** Default cap on `stderrTail`, in BYTES of UTF-8. */
export const STDERR_TAIL_BYTES = 4096;
/** Maximum stdout snippet included in `envelope_missing` messages. */
const STDOUT_PREVIEW_BYTES = 512;
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
export function tailStderrBytes(text, limit = STDERR_TAIL_BYTES) {
    if (!text)
        return undefined;
    if (limit === null)
        return text;
    const cap = limit === undefined ? STDERR_TAIL_BYTES : limit;
    if (cap <= 0)
        return undefined;
    const raw = Buffer.from(text, "utf-8");
    if (raw.length <= cap)
        return text;
    let start = raw.length - cap;
    // A UTF-8 continuation byte matches 0b10xxxxxx; a lead byte never does. The
    // source is a valid string, so at most 3 continuation bytes can precede the
    // first lead byte in the window.
    while (start < raw.length && (raw[start] & 0xc0) === 0x80) {
        start += 1;
    }
    return raw.subarray(start).toString("utf-8");
}
const VALID_CLASSIFICATIONS = new Set([
    "transport",
    "protocol",
    "engine",
    "approval",
    "unknown",
]);
/**
 * The envelope `metadata` keys that make up a usage report. Presence of at
 * least one of them is what distinguishes "the engine reported usage" from
 * "this engine predates protocol 0.4.0 and reported none".
 */
const USAGE_KEYS = [
    "tokensIn",
    "tokensOut",
    "cacheReadTokens",
    "cacheWriteTokens",
    "costUsd",
];
/**
 * Coerce a wire token count to a number, defaulting to 0.
 *
 * Deliberately total: a malformed count must not be able to turn a completed
 * turn into a throw on the host's side.
 */
function toInt(value) {
    if (typeof value === "number" && Number.isFinite(value))
        return Math.trunc(value);
    if (typeof value === "string") {
        const parsed = Number(value);
        if (Number.isFinite(parsed))
            return Math.trunc(parsed);
    }
    return 0;
}
/**
 * Read the envelope's `metadata` usage block into a `Usage`.
 *
 * Returns `undefined` when the metadata carries none of the usage keys at all
 * — an engine older than protocol 0.4.0 never reported them, and claiming
 * zeroes on its behalf would be a fabricated number rather than an absent one.
 */
function usageFromMetadata(metadata) {
    if (!USAGE_KEYS.some((key) => key in metadata))
        return undefined;
    // costUsd stays a STRING on the TS side. See the `Usage` doc comment in
    // session.ts for why this is a deliberate parity exception.
    const rawCost = metadata.costUsd;
    const costUsd = typeof rawCost === "string" ? rawCost : rawCost == null ? null : String(rawCost);
    return {
        // Mirrors the envelope verbatim: tokensIn is the CHARGED total the engine
        // already computed. No wrapper-side arithmetic.
        inputTokens: toInt(metadata.tokensIn),
        outputTokens: toInt(metadata.tokensOut),
        cacheReadTokens: toInt(metadata.cacheReadTokens),
        cacheWriteTokens: toInt(metadata.cacheWriteTokens),
        costUsd,
    };
}
/**
 * Validate that `parsed` conforms to the §4.1 envelope shape.
 *
 * Required:
 *   - protocolVersion, sessionId, turnId, reply: string
 *   - error: null | object with `code: string`
 *   - metadata: object
 *
 * Partial / type-wrong envelopes return `false` so the caller falls to Rule 2.
 */
function isShapeValid(parsed) {
    if (parsed === null || typeof parsed !== "object")
        return false;
    const o = parsed;
    if (typeof o.protocolVersion !== "string")
        return false;
    if (typeof o.sessionId !== "string")
        return false;
    if (typeof o.turnId !== "string")
        return false;
    if (typeof o.reply !== "string")
        return false;
    if (typeof o.metadata !== "object" || o.metadata === null)
        return false;
    if (o.error === null)
        return true;
    if (typeof o.error !== "object")
        return false;
    const err = o.error;
    if (typeof err.code !== "string")
        return false;
    return true;
}
/**
 * Parse a subprocess outcome into a single DisplayEvent.
 *
 * See module docstring for precedence rules.
 */
export function parseRunOutput(outcome, options = {}) {
    const tailBytes = options.stderrTailBytes;
    const fallbackSessionId = options.fallbackSessionId;
    const trimmed = outcome.stdout.trim();
    // Attempt to parse stdout as JSON. Failures (empty, partial, non-JSON) are
    // captured silently; the caller falls to Rule 2.
    let parsed = null;
    if (trimmed.length > 0) {
        try {
            parsed = JSON.parse(trimmed);
        }
        catch {
            parsed = null;
        }
    }
    // Rule 1 — envelope parseable per §4.1 → envelope wins.
    if (parsed !== null && isShapeValid(parsed)) {
        const env = parsed;
        const usage = usageFromMetadata(env.metadata);
        if (env.error === null) {
            // Success path — exit code is informational only, but still reported as
            // observed so a host can see a post-flush crash (a result event carrying
            // a non-zero exitCode).
            const stderrTail = tailStderrBytes(outcome.stderr, tailBytes);
            return {
                type: "result",
                text: env.reply,
                sessionId: env.sessionId,
                turnId: env.turnId,
                exitCode: outcome.exitCode,
                ...(usage !== undefined ? { usage } : {}),
                ...(stderrTail !== undefined ? { stderrTail } : {}),
            };
        }
        // Failure path — populate from the envelope's error fields.
        const err = env.error;
        const classification = err.classification !== undefined &&
            VALID_CLASSIFICATIONS.has(err.classification)
            ? err.classification
            : "unknown";
        const severity = err.severity === "warning" ? "warning" : "error";
        const correlationId = typeof err.correlationId === "string" ? err.correlationId : "";
        const message = typeof err.message === "string" ? err.message : err.code;
        const sourceTail = typeof err.stderrTail === "string" ? err.stderrTail : outcome.stderr;
        const stderrTail = tailStderrBytes(sourceTail, tailBytes);
        return {
            type: "error",
            code: err.code,
            classification,
            severity,
            correlationId,
            message,
            ...(stderrTail !== undefined ? { stderrTail } : {}),
            retryable: false,
            sessionId: env.sessionId,
            turnId: env.turnId,
            exitCode: outcome.exitCode,
            ...(usage !== undefined ? { usage } : {}),
        };
    }
    // Rule 2 — envelope absent or unparseable → synthesize from exit + stderr.
    // No envelope means no turnId and no usage to report: the engine assigns the
    // turn id and nothing came back, so inventing one would be a fabrication.
    // The SESSION id is different — the caller minted it and passed it in as
    // `fallbackSessionId`, so a host correlating this failure against its own
    // records still gets the handle it already knows. The exit code remains
    // load-bearing for the code/classification split below.
    const stderrTail = tailStderrBytes(outcome.stderr, tailBytes);
    const sessionIdField = fallbackSessionId !== undefined ? { sessionId: fallbackSessionId } : {};
    if (outcome.exitCode === 0) {
        // Engine protocol violation: exit 0 without a parseable envelope.
        const preview = outcome.stdout.slice(0, STDOUT_PREVIEW_BYTES);
        const previewSuffix = outcome.stdout.length > STDOUT_PREVIEW_BYTES ? "...(truncated)" : "";
        return {
            type: "error",
            code: "envelope_missing",
            classification: "protocol",
            severity: "error",
            correlationId: "",
            message: `Engine exited 0 without emitting a parseable §4.1 envelope. Stdout was: ${JSON.stringify(preview)}${previewSuffix}`,
            ...(stderrTail !== undefined ? { stderrTail } : {}),
            retryable: false,
            ...sessionIdField,
            exitCode: outcome.exitCode,
        };
    }
    // Non-zero exit, envelope absent or partial — engine-class failure.
    return {
        type: "error",
        code: `engine_exit_${outcome.exitCode}`,
        classification: "engine",
        severity: "error",
        correlationId: "",
        message: `Engine exited ${outcome.exitCode} without emitting a parseable §4.1 envelope.`,
        ...(stderrTail !== undefined ? { stderrTail } : {}),
        retryable: false,
        ...sessionIdField,
        exitCode: outcome.exitCode,
    };
}
