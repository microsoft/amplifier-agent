"""Fixtures for the modes activation suite: seed a custom mode file into the DTU.

Pushes our test mode fixture into the running DTU at test time via ``dtu.push_file`` and
returns the in-DTU launch directory the custom-mode case runs from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# In-DTU launch dir for the custom-mode case; the mode is discovered from its .amplifier/modes/.
WS_MODES = "/root/e2e/ws-modes"


@pytest.fixture
def seeded_mode(dtu_id: str) -> str:
    """Seed a custom mode in the launch directory ``WS_MODES/.amplifier/modes/`` and return WS_MODES."""
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "e2e-probe-mode.md"),
        f"{WS_MODES}/.amplifier/modes/e2e-probe-mode.md",
    )
    return WS_MODES
