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

``ConfigError`` is defined in ``loader.py`` (rather than this module) so
that ``loader.py`` can raise it without a circular import; we re-export
it here so callers continue to use ``from amplifier_agent_lib.config
import ConfigError``.
"""

from __future__ import annotations

from amplifier_agent_lib.config.loader import ConfigError, load_config
from amplifier_agent_lib.config.merger import merge_config

__all__ = ["ConfigError", "load_config", "merge_config"]
