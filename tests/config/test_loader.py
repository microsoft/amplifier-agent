"""Tests for amplifier_agent_lib.config package skeleton (B1).

Verifies that ConfigError is a proper AaaError subclass that propagates
code/classification/message correctly so the CLI's existing
_build_error_envelope path emits a §4.1 envelope with
classification='protocol' (exit code 2 per _EXIT_CODE_BY_CLASSIFICATION).
"""

from __future__ import annotations

from amplifier_agent_lib.config import ConfigError
from amplifier_agent_lib.protocol.errors import AaaError


def test_config_error_is_aaa_error_subclass() -> None:
    assert issubclass(ConfigError, AaaError)


def test_config_error_carries_code_classification_message() -> None:
    exc = ConfigError(
        code="config_unreadable",
        message="not found",
        classification="protocol",
    )
    assert exc.code == "config_unreadable"
    assert exc.classification == "protocol"
    assert exc.message == "not found"
