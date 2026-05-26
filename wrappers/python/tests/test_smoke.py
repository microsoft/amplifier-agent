"""Smoke test: package is importable and exports the correct protocol version constant.

Verifies that `amplifier_agent_client` is correctly installed as a workspace
member and that the protocol version constant is properly exported.
Public API is built up across Tasks 4-12; this test guards the package skeleton.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_protocol_version_constant_is_exported() -> None:
    """Package exports PROTOCOL_VERSION_REQUIRED_BY_WRAPPER with the correct value.

    Regression guard: the constant must be importable and match the protocol
    version string '0.1.0' that this wrapper targets.
    """
    from amplifier_agent_client import PROTOCOL_VERSION_REQUIRED_BY_WRAPPER

    assert PROTOCOL_VERSION_REQUIRED_BY_WRAPPER == "0.1.0", (
        f"Protocol version mismatch: got {PROTOCOL_VERSION_REQUIRED_BY_WRAPPER!r}, expected '0.1.0'"
    )
