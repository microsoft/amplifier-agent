"""run_output_parser.py — parse the Mode A v2 subprocess outcome into a DisplayEvent.

Implements §4.1 envelope schema and §4.4 (SC-D) precedence rules from
docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md:

  Rule 1 — envelope parseable per §4.1 → envelope is authoritative.
    The ``error`` field (None or populated) drives the wrapper's outcome.
    The exit code is informational and does NOT override the envelope.

  Rule 2 — envelope absent / unparseable / partial → synthesize an error
           event from exit code and stderr tail. Partial JSON is NOT
           half-parsed (belt-and-suspenders): if any required §4.1 field
           is missing, the envelope is treated as unparseable.

stderr_tail is truncated to STDERR_TAIL_BYTES (4096) on synthesized paths;
on the envelope path it is taken verbatim from the engine.
"""

from __future__ import annotations

import json
from typing import Any

from amplifier_agent_client.types import DisplayEvent

#: Maximum stderrTail length retained on synthesized engine errors.
STDERR_TAIL_BYTES = 4096

#: Maximum stdout snippet included in ``envelope_missing`` messages.
_STDOUT_PREVIEW_BYTES = 512

#: Allowed values for ``error.classification`` per §4.1.
_VALID_CLASSIFICATIONS: frozenset[str] = frozenset({"transport", "protocol", "engine", "approval", "unknown"})


def _tail_stderr(stderr: str) -> str | None:
    """Keep the last STDERR_TAIL_BYTES chars of stderr.

    Returns None for an empty string so callers can omit the field cleanly
    when there is nothing to surface.
    """
    if not stderr:
        return None
    if len(stderr) <= STDERR_TAIL_BYTES:
        return stderr
    return stderr[-STDERR_TAIL_BYTES:]


def _is_shape_valid(parsed: Any) -> bool:
    """Validate that ``parsed`` conforms to the §4.1 envelope shape.

    Required:
      - protocolVersion, sessionId, turnId, reply: str
      - error: None | object with code: str
      - metadata: dict

    Partial / type-wrong envelopes return False so the caller falls to Rule 2.
    """
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
    if not isinstance(err.get("code"), str):
        return False
    return True


def parse_run_output(outcome: dict[str, Any]) -> DisplayEvent:
    """Parse a subprocess outcome into a single DisplayEvent.

    Args:
        outcome: dict with keys ``stdout`` (str), ``stderr`` (str),
                 ``exitCode`` (int).

    See module docstring for precedence rules.
    """
    stdout: str = outcome.get("stdout", "") or ""
    stderr: str = outcome.get("stderr", "") or ""
    exit_code: int = int(outcome.get("exitCode", -1))

    trimmed = stdout.strip()

    # Attempt to parse stdout as JSON. Failures (empty, partial, non-JSON)
    # are captured silently; the caller falls to Rule 2.
    parsed: Any = None
    if len(trimmed) > 0:
        try:
            parsed = json.loads(trimmed)
        except (json.JSONDecodeError, ValueError):
            parsed = None

    # Rule 1 — envelope parseable per §4.1 → envelope wins.
    if parsed is not None and _is_shape_valid(parsed):
        env: dict[str, Any] = parsed

        if env.get("error") is None:
            # Success path — exit code is informational only.
            return {"type": "result", "text": env["reply"]}

        # Failure path — populate from the envelope's error fields.
        err: dict[str, Any] = env["error"]
        classification_raw = err.get("classification")
        classification: Any = (
            classification_raw
            if isinstance(classification_raw, str) and classification_raw in _VALID_CLASSIFICATIONS
            else "unknown"
        )
        severity_raw = err.get("severity")
        severity: Any = "warning" if severity_raw == "warning" else "error"
        correlation_id = err["correlationId"] if isinstance(err.get("correlationId"), str) else ""
        message = err["message"] if isinstance(err.get("message"), str) else err["code"]
        stderr_tail = err["stderrTail"] if isinstance(err.get("stderrTail"), str) else _tail_stderr(stderr)

        event: DisplayEvent = {
            "type": "error",
            "code": err["code"],
            "classification": classification,
            "severity": severity,
            "correlationId": correlation_id,
            "message": message,
            "retryable": False,
        }
        if stderr_tail is not None:
            event["stderrTail"] = stderr_tail
        return event

    # Rule 2 — envelope absent or unparseable → synthesize from exit + stderr.
    stderr_tail = _tail_stderr(stderr)

    if exit_code == 0:
        # Engine protocol violation: exit 0 without a parseable envelope.
        preview = stdout[:_STDOUT_PREVIEW_BYTES]
        preview_suffix = "...(truncated)" if len(stdout) > _STDOUT_PREVIEW_BYTES else ""
        ev: DisplayEvent = {
            "type": "error",
            "code": "envelope_missing",
            "classification": "protocol",
            "severity": "error",
            "correlationId": "",
            "message": (
                f"Engine exited 0 without emitting a parseable §4.1 envelope. "
                f"Stdout was: {json.dumps(preview)}{preview_suffix}"
            ),
            "retryable": False,
        }
        if stderr_tail is not None:
            ev["stderrTail"] = stderr_tail
        return ev

    # Non-zero exit, envelope absent or partial — engine-class failure.
    ev2: DisplayEvent = {
        "type": "error",
        "code": f"engine_exit_{exit_code}",
        "classification": "engine",
        "severity": "error",
        "correlationId": "",
        "message": f"Engine exited {exit_code} without emitting a parseable §4.1 envelope.",
        "retryable": False,
    }
    if stderr_tail is not None:
        ev2["stderrTail"] = stderr_tail
    return ev2
