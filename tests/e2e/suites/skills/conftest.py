"""Fixtures for the skills invocation suite: seed skill files into the DTU.

These push our test skill fixtures into the running DTU at test time via
``dtu.push_file`` and return the in-DTU paths the cases launch against.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# In-DTU paths.
WS = "/root/e2e/ws-skills"  # launch dir for the custom-skill case
EXTRA_SKILLS_DIR = "/root/e2e/extra-skills"  # configured (non-launch) skills location
CFG_SKILLS = "/root/e2e/host-config-skills.json"


@pytest.fixture
def seeded_workspace(dtu_id: str) -> str:
    """Seed a skill in the launch directory ``WS/.amplifier/skills/`` and return WS."""
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "e2e-crusty-probe" / "SKILL.md"),
        f"{WS}/.amplifier/skills/e2e-crusty-probe/SKILL.md",
    )
    return WS


@pytest.fixture
def configured_skills(dtu_id: str) -> str:
    """Seed a skill in a non-launch dir + push a host-config pointing at it. Returns CFG_SKILLS."""
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "e2e-configured-probe" / "SKILL.md"),
        f"{EXTRA_SKILLS_DIR}/e2e-configured-probe/SKILL.md",
    )
    dtu.push_file(dtu_id, str(FIXTURES / "host-config-skills.json"), CFG_SKILLS)
    return CFG_SKILLS
