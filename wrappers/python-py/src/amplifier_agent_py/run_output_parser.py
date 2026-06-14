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
           is missing, the envelope is treated as unparseable.

``stderr_tail`` is truncated to ``STDERR_TAIL_BYTES`` (4096) on synthesized
paths; on the envelope path it is taken verbatim from the engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from .errors import Classification
from .types import DisplayEvent, ErrorEvent, ResultEvent

#: Maximum stderr_tail length retained on synthesized engine errors.
STDERR_TAIL_BYTES = 4096

#: Maximum stdout snippet included in ``envelope_missing`` messages.
_STDOUT_PREVIEW_BYTES = 512

_VALID_CLASSIFICATIONS: frozenset[str] = frozenset({"transport", "protocol", "engine", "approval", "unknown"})


@dataclass(frozen=True, kw_only=True)
class SubprocessOutcome:
    """Outcome of running the ``amplifier-agent run --output json`` subprocess."""

    stdout: str
    stderr: str
    exit_code: int


def _tail_stderr(stderr: str) -> str | None:
    """Keep the last ``STDERR_TAIL_BYTES`` chars of ``stderr`` or ``None``."""
    if not stderr:
        return None
    if len(stderr) <= STDERR_TAIL_BYTES:
        return stderr
    return stderr[-STDERR_TAIL_BYTES:]


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


def parse_run_output(outcome: SubprocessOutcome) -> DisplayEvent:
    """Parse a subprocess outcome into a single ``DisplayEvent``.

    See module docstring for precedence rules.
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

        err = env.get("error")
        if err is None:
            return ResultEvent(text=cast(str, env["reply"]))

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
        if isinstance(envelope_tail, str):
            stderr_tail: str | None = envelope_tail
        else:
            stderr_tail = _tail_stderr(outcome.stderr)

        return ErrorEvent(
            code=cast(str, err_dict["code"]),
            classification=classification,
            severity=severity,
            correlation_id=correlation_id,
            message=message,
            stderr_tail=stderr_tail,
            retryable=False,
        )

    # Rule 2 — envelope absent or unparseable → synthesize from exit + stderr.
    stderr_tail = _tail_stderr(outcome.stderr)

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
        )

    return ErrorEvent(
        code=f"engine_exit_{outcome.exit_code}",
        classification="engine",
        severity="error",
        correlation_id="",
        message=f"Engine exited {outcome.exit_code} without emitting a parseable §4.1 envelope.",
        stderr_tail=stderr_tail,
        retryable=False,
    )
