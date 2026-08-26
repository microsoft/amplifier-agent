"""Parse the Mode A v2 subprocess outcome into a single ``DisplayEvent``.

Mirrors wrappers/typescript/src/run-output-parser.ts.

Implements §4.1 envelope schema and §4.4 (SC-D) precedence rules from
``docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md``:

  Rule 1 — envelope parseable per §4.1 → envelope is authoritative.
    The ``error`` field (null or populated) drives the wrapper's outcome.
    The exit code is informational and does NOT override the envelope.

  Rule 2 — envelope absent / unparseable / partial → synthesize an error
           event from exit code and stderr tail.  Partial JSON is NOT
           half-parsed (belt-and-suspenders): if any required §4.1 field
           is missing, the envelope is treated as unparseable.  The turn id
           is unknowable here (the engine assigns it), but the SESSION id is
           the caller's own -- pass it as ``fallback_session_id`` and it is
           reported rather than dropped.

On the Rule 1 path the envelope is now *read*, not merely shape-checked:
``sessionId``, ``turnId`` and the ``metadata`` usage block are surfaced on the
terminal event.  The wrapper performs no arithmetic of its own -- the engine's
``UsageAccumulator`` already summed the turn, so re-summing here would
double-count.

``stderr_tail`` is bounded by ``stderr_tail_bytes`` in real UTF-8 BYTES (see
``tail_stderr_bytes``); ``STDERR_TAIL_BYTES`` (4096) is the default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from .errors import Classification
from .types import DisplayEvent, ErrorEvent, ResultEvent, Usage

#: Default cap on ``stderr_tail``, in BYTES of UTF-8.
STDERR_TAIL_BYTES = 4096

#: Maximum stdout snippet included in ``envelope_missing`` messages.
_STDOUT_PREVIEW_BYTES = 512

_VALID_CLASSIFICATIONS: frozenset[str] = frozenset({"transport", "protocol", "engine", "approval", "unknown"})

#: The envelope ``metadata`` keys that make up a usage report.  Presence of at
#: least one of them is what distinguishes "the engine reported usage" from
#: "this engine predates protocol 0.4.0 and reported none".
_USAGE_KEYS: tuple[str, ...] = (
    "tokensIn",
    "tokensOut",
    "cacheReadTokens",
    "cacheWriteTokens",
    "costUsd",
)


@dataclass(frozen=True, kw_only=True)
class SubprocessOutcome:
    """Outcome of running the ``amplifier-agent run --output json`` subprocess."""

    stdout: str
    stderr: str
    exit_code: int


def tail_stderr_bytes(text: str, limit: int | None = STDERR_TAIL_BYTES) -> str | None:
    """Return the last ``limit`` BYTES of ``text``, never splitting a codepoint.

    The cap is expressed in bytes because that is what a host budgeting a log
    line or a payload actually cares about; a character count is meaningless
    for that purpose the moment stderr contains non-ASCII.

    ``limit`` semantics:

    * a positive int -- at most that many UTF-8 bytes, taken from the END.
    * ``None``       -- the entire buffer, uncapped.
    * ``0`` or less  -- capture disabled; returns ``None``.

    An empty ``text`` always yields ``None``: there is nothing to report.

    Boundary safety: after slicing the encoded buffer, leading UTF-8
    continuation bytes (``0b10xxxxxx``) are dropped so the slice begins on a
    lead byte.  The result therefore decodes strictly -- no ``U+FFFD``, no
    ``UnicodeDecodeError`` -- at the cost of up to 3 bytes fewer than ``limit``.
    Returning slightly less than the cap is correct; returning a broken
    character is not.
    """
    if not text:
        return None
    if limit is None:
        return text
    if limit <= 0:
        return None

    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text

    window = raw[-limit:]
    start = 0
    # A UTF-8 continuation byte matches 0b10xxxxxx; a lead byte never does. The
    # source is a valid str, so at most 3 continuation bytes can precede the
    # first lead byte in the window.
    while start < len(window) and (window[start] & 0xC0) == 0x80:
        start += 1
    return window[start:].decode("utf-8")


def _is_shape_valid(parsed: Any) -> bool:
    """Validate that ``parsed`` conforms to the §4.1 envelope shape."""
    if not isinstance(parsed, dict):
        return False
    if not isinstance(parsed.get("protocolVersion"), str):
        return False
    if not isinstance(parsed.get("sessionId"), str):
        return False
    if not isinstance(parsed.get("turnId"), str):
        return False
    if not isinstance(parsed.get("reply"), str):
        return False
    if not isinstance(parsed.get("metadata"), dict):
        return False

    err = parsed.get("error")
    if err is None:
        return True
    if not isinstance(err, dict):
        return False
    return isinstance(err.get("code"), str)


def _to_int(value: Any) -> int:
    """Coerce a wire token count to an int, defaulting to 0.

    Deliberately total: a malformed count must not be able to turn a completed
    turn into an exception on the host's side.
    """
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_decimal(value: Any) -> Decimal | None:
    """Parse the wire ``costUsd`` string into a ``Decimal``, or ``None``.

    ``str()`` first, always: ``Decimal(0.1)`` captures the binary float's error,
    ``Decimal("0.1")`` does not.  A cost that will not parse is reported as
    absent rather than as zero.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _usage_from_metadata(metadata: Any) -> Usage | None:
    """Read the envelope's ``metadata`` usage block into a ``Usage``.

    Returns ``None`` when the metadata carries none of the usage keys at all --
    an engine older than protocol 0.4.0 never reported them, and claiming zeroes
    on its behalf would be a fabricated number rather than an absent one.
    """
    if not isinstance(metadata, dict):
        return None
    meta = cast(dict[str, Any], metadata)
    if not any(key in meta for key in _USAGE_KEYS):
        return None

    return Usage(
        # Mirrors the envelope verbatim: tokensIn is the CHARGED total the
        # engine already computed. No wrapper-side arithmetic.
        input_tokens=_to_int(meta.get("tokensIn")),
        output_tokens=_to_int(meta.get("tokensOut")),
        cache_read_tokens=_to_int(meta.get("cacheReadTokens")),
        cache_write_tokens=_to_int(meta.get("cacheWriteTokens")),
        cost_usd=_to_decimal(meta.get("costUsd")),
    )


def parse_run_output(
    outcome: SubprocessOutcome,
    *,
    stderr_tail_bytes: int | None = STDERR_TAIL_BYTES,
    fallback_session_id: str | None = None,
) -> DisplayEvent:
    """Parse a subprocess outcome into a single ``DisplayEvent``.

    See module docstring for precedence rules.

    Args:
        outcome: stdout / stderr / exit code of the finished subprocess.
        stderr_tail_bytes: byte cap for ``stderr_tail`` on the returned event.
            Positive int caps to that many UTF-8 bytes, ``None`` keeps the whole
            buffer, ``0`` disables the field.  The cap applies to whatever ends
            up in ``stderr_tail``, including a tail the engine supplied in the
            envelope, so ``0`` really does mean "do not give me stderr".
        fallback_session_id: the caller's own session id, reported on the
            SYNTHESIZED (Rule 2) events only.  Never overrides the envelope:
            on the Rule 1 path the envelope's ``sessionId`` is authoritative
            and this argument is ignored.  ``None`` (the default) preserves
            the previous behaviour of leaving the field unset -- a caller with
            no session id to offer, such as a host post-parsing a captured
            payload, is not made to invent one.
    """
    trimmed = outcome.stdout.strip()

    parsed: Any = None
    if trimmed:
        try:
            parsed = json.loads(trimmed)
        except (json.JSONDecodeError, ValueError):
            parsed = None

    # Rule 1 — envelope parseable per §4.1 → envelope wins.
    if parsed is not None and _is_shape_valid(parsed):
        env = cast(dict[str, Any], parsed)
        session_id = cast(str, env["sessionId"])
        turn_id = cast(str, env["turnId"])
        usage = _usage_from_metadata(env.get("metadata"))

        err = env.get("error")
        if err is None:
            return ResultEvent(
                text=cast(str, env["reply"]),
                session_id=session_id,
                turn_id=turn_id,
                # Informational per SC-D: the envelope already decided the
                # outcome. Reported as observed so a host can still see a
                # post-flush crash (a result event with a non-zero exit).
                exit_code=outcome.exit_code,
                usage=usage,
                stderr_tail=tail_stderr_bytes(outcome.stderr, stderr_tail_bytes),
            )

        # Failure path — populate from the envelope's error fields.
        err_dict = cast(dict[str, Any], err)
        raw_class = err_dict.get("classification")
        classification: Classification = (
            cast(Classification, raw_class) if raw_class in _VALID_CLASSIFICATIONS else "unknown"
        )
        severity = "warning" if err_dict.get("severity") == "warning" else "error"
        correlation_id_raw = err_dict.get("correlationId")
        correlation_id = correlation_id_raw if isinstance(correlation_id_raw, str) else ""
        message_raw = err_dict.get("message")
        message = message_raw if isinstance(message_raw, str) else cast(str, err_dict["code"])

        envelope_tail = err_dict.get("stderrTail")
        source_tail = envelope_tail if isinstance(envelope_tail, str) else outcome.stderr
        stderr_tail = tail_stderr_bytes(source_tail, stderr_tail_bytes)

        return ErrorEvent(
            code=cast(str, err_dict["code"]),
            classification=classification,
            severity=severity,
            correlation_id=correlation_id,
            message=message,
            stderr_tail=stderr_tail,
            retryable=False,
            session_id=session_id,
            turn_id=turn_id,
            exit_code=outcome.exit_code,
            usage=usage,
        )

    # Rule 2 — envelope absent or unparseable → synthesize from exit + stderr.
    # No envelope means no turnId and no usage to report: the engine assigns the
    # turn id and nothing came back, so inventing one would be a fabrication.
    # The SESSION id is different -- the caller minted it and passed it in as
    # ``fallback_session_id``, so a host correlating this failure against its own
    # records still gets the handle it already knows. The exit code remains
    # load-bearing for the code/classification split below.
    stderr_tail = tail_stderr_bytes(outcome.stderr, stderr_tail_bytes)

    if outcome.exit_code == 0:
        preview = outcome.stdout[:_STDOUT_PREVIEW_BYTES]
        preview_suffix = "...(truncated)" if len(outcome.stdout) > _STDOUT_PREVIEW_BYTES else ""
        return ErrorEvent(
            code="envelope_missing",
            classification="protocol",
            severity="error",
            correlation_id="",
            message=(
                f"Engine exited 0 without emitting a parseable §4.1 envelope. "
                f"Stdout was: {json.dumps(preview)}{preview_suffix}"
            ),
            stderr_tail=stderr_tail,
            retryable=False,
            session_id=fallback_session_id,
            exit_code=outcome.exit_code,
        )

    return ErrorEvent(
        code=f"engine_exit_{outcome.exit_code}",
        classification="engine",
        severity="error",
        correlation_id="",
        message=f"Engine exited {outcome.exit_code} without emitting a parseable §4.1 envelope.",
        stderr_tail=stderr_tail,
        retryable=False,
        session_id=fallback_session_id,
        exit_code=outcome.exit_code,
    )
