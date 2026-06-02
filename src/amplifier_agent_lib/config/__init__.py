"""Configuration loading and merging for amplifier-agent.

This package owns reading and composing layered host configuration:

* ``loader.py`` — discovers the on-disk config file, reads it from the
  conventional XDG location, and parses it into raw layer dicts.
* ``merger.py`` — combines the discovered layers with hardcoded defaults
  and CLI overrides into the effective configuration consumed by the
  engine and CLI.

All recoverable failures surfaced by this package are raised as
``ConfigError``.  Because ``ConfigError`` subclasses
:class:`amplifier_agent_lib.protocol.errors.AaaError`, the CLI's existing
``_build_error_envelope`` path catches it and emits a §4.1 error envelope
with ``classification='protocol'`` (exit code 2 per
``_EXIT_CODE_BY_CLASSIFICATION``).
"""

from __future__ import annotations

from amplifier_agent_lib.protocol.errors import AaaError

__all__ = ["ConfigError"]


class ConfigError(AaaError):
    """Recoverable configuration error raised by loader/merger.

    Subclasses :class:`AaaError` so the CLI's existing error-envelope
    machinery catches it and emits a §4.1 envelope.  Defaults
    ``classification`` to ``"protocol"`` so the wrapper exits with the
    protocol exit code (2) unless the caller explicitly overrides it.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        classification: str | None = "protocol",
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            classification=classification,
        )
