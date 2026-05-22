"""Wire-level error codes for JSON-RPC error.data.code field.

Unifies design Appendix A 'Error codes' with Phase 1 spec additions and the
v0.1.0 (A1) AaaError extensions per design §4.10.2.
Each value is the exact string that appears on the wire.
"""

from __future__ import annotations

from enum import StrEnum


class AaaError(Exception):
    """Domain error raised by the amplifier-agent engine or CLI layer.

    Carries a string error code (matching an ErrorCode value) and a
    human-readable message.  The CLI layer catches this to emit a JSON
    error envelope ``{'error': {'code': ..., 'message': ...}}`` on stdout.

    v0.1.0 (A1) adds optional severity / classification / correlation_id /
    stderr_tail fields per design §4.10.2.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        severity: str | None = None,
        classification: str | None = None,
        correlation_id: str | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.severity = severity
        self.classification = classification
        self.correlation_id = correlation_id
        self.stderr_tail = stderr_tail


class ErrorCode(StrEnum):
    """Wire-level error codes for the JSON-RPC ``error.data.code`` field."""

    # ------------------------------------------------------------------
    # Lifecycle / session
    # ------------------------------------------------------------------
    AGENT_NOT_READY = "agent_not_ready"
    INVALID_SESSION = "invalid_session"
    STALE_SESSION = "stale_session"
    SESSION_NOT_FOUND = "session_not_found"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    CONFIG_VALIDATION = "config_validation"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_INIT_FAILED = "provider_init_failed"
    PROMPT_REQUIRED = "prompt_required"

    # ------------------------------------------------------------------
    # Bundle / spawn
    # ------------------------------------------------------------------
    BUNDLE_LOAD_FAILED = "bundle_load_failed"
    SPAWN_FAILED = "spawn_failed"

    # ------------------------------------------------------------------
    # Approval (v0.1.0)
    # ------------------------------------------------------------------
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_TIMEOUT = "approval_timeout"
    APPROVAL_TRANSLATION_FAILED = "approval_translation_failed"
    APPROVAL_PROTOCOL_VIOLATION = "approval_protocol_violation"

    # ------------------------------------------------------------------
    # Wrapper security (v0.1.0)
    # ------------------------------------------------------------------
    ENV_INJECTION_REJECTED = "env_injection_rejected"

    # ------------------------------------------------------------------
    # Tool / runtime
    # ------------------------------------------------------------------
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    RUNTIME = "runtime"

    # ------------------------------------------------------------------
    # Wire protocol
    # ------------------------------------------------------------------
    WIRE_PROTOCOL_VIOLATION = "wire_protocol_violation"
    PROTOCOL_VERSION_MISMATCH = "protocol_version_mismatch"

    # ------------------------------------------------------------------
    # Catch-all
    # ------------------------------------------------------------------
    INTERNAL = "internal"
