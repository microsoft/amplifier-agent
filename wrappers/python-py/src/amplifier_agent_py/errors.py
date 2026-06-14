"""Typed errors for the Amplifier Agent Python wrapper.

Mirrors the TypeScript wrapper's `AaaError` class shape (wrappers/typescript/src/session.ts).
Same `code` strings, same `classification` values, same `severity` values so Python
hosts can pattern-match the wrapper's errors the same way TS hosts do.
"""

from __future__ import annotations

from typing import Literal

# Public type aliases mirroring the TS DisplayEvent error classification fields.
Classification = Literal["transport", "protocol", "engine", "approval", "unknown"]
Severity = Literal["error", "warning"]


class AaaError(Exception):
    """Typed error for AaA wrapper lifecycle and protocol violations.

    Mirrors `AaaError` from wrappers/typescript/src/session.ts.

    Attributes:
        code:           Stable identifier (e.g. ``"binary_not_found"``,
                        ``"protocol_version_mismatch"``).  Same values as the TS
                        wrapper emits.
        remediation:    Human-readable description of what went wrong and how
                        to fix it.  Becomes the exception message.
        classification: One of ``"transport"``, ``"protocol"``, ``"engine"``,
                        ``"approval"``, ``"unknown"``.
        severity:       ``"error"`` or ``"warning"``.
        correlation_id: Engine-supplied correlation id (when surfaced from the
                        engine envelope).
        stderr_tail:    Last few KB of subprocess stderr (when relevant).
    """

    code: str
    remediation: str | None
    classification: Classification | None
    severity: Severity | None
    correlation_id: str | None
    stderr_tail: str | None

    def __init__(
        self,
        code: str,
        remediation: str | None = None,
        *,
        classification: Classification | None = None,
        severity: Severity | None = None,
        correlation_id: str | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(remediation if remediation is not None else code)
        self.code = code
        self.remediation = remediation
        self.classification = classification
        self.severity = severity
        self.correlation_id = correlation_id
        self.stderr_tail = stderr_tail

    def __repr__(self) -> str:
        return f"AaaError(code={self.code!r}, classification={self.classification!r}, severity={self.severity!r})"
