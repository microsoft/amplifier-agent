"""Fixtures for the launch-dir suite: seed a probe mode and a probe skill into the DTU.

The contract under test is one sentence: when ``--cwd`` is NOT passed, the directory the
command was LAUNCHED FROM is the working directory, and BOTH modes and skills are
discovered from it.

Everything here is seeded under ``/root/e2e/...`` launch directories and NOTHING is
written into the DTU home directory. The home dir is also the HTTP server's launch dir
and is shared with every other suite, so a seed there would leak into listings that
never opted into it (see ``suites/shadowing/conftest.py``, which has to clean up after
itself for exactly that reason). Launch-dir-only seeds are invisible to anything started
elsewhere, so this suite needs no teardown at all.

Both probe names are unique and collide with NO built-in. That is deliberate: a collision
would drag mode/skill PRECEDENCE into the result, and precedence is already pinned by
``suites/shadowing/``. Here the only question is whether the launch directory is searched
at all.

Each probe body carries a sentinel token that exists in exactly ONE fixture file, so
seeing it in a turn's output proves that specific file's body reached the model. Same
technique as ``SIGIL_SENTINEL`` in ``suites/skills/conftest.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# --------------------------------------------------------------------------- #
# In-DTU paths and names
# --------------------------------------------------------------------------- #

# Host-config seeded into every DTU by provisioning (anthropic provider, approval "yes").
CONFIG = "/root/e2e/host-config.json"

# Launch dirs, one per probe so a case's seed can never be confused for another's.
#
# BOTH mode cases share WS_MODE on purpose. They seed the same file, run the same
# command from the same directory, and assert the same sentinel; the ONLY difference
# between them is whether `--cwd` is passed explicitly. Giving them separate workspaces
# would have introduced a second difference and weakened the control.
WS_MODE = "/root/e2e/ws-launchdir-mode"
WS_SKILL = "/root/e2e/ws-launchdir-skill"

# Names under test. Neither collides with a built-in (`plan`, `brainstorm`, `code-review`,
# `council`), so discovery is the only variable.
PROBE_MODE = "e2e-launchdir-probe-mode"
PROBE_SKILL = "e2e-launchdir-probe-skill"

# Lives ONLY in fixtures/e2e-launchdir-probe-mode.md.
MODE_SENTINEL = "LAUNCHDIR-MODE-ACTIVE-M3T7"

# Lives ONLY in fixtures/e2e-launchdir-probe-skill/SKILL.md.
SKILL_SENTINEL = "LAUNCHDIR-SKILL-RAN-S8V4"


# --------------------------------------------------------------------------- #
# Seeds
# --------------------------------------------------------------------------- #


@pytest.fixture
def launchdir_mode(dtu_id: str) -> str:
    """Seed the probe mode into ``WS_MODE/.amplifier/modes/`` and return WS_MODE.

    The fixture's frontmatter shape is copied from the real built-in
    ``src/amplifier_agent_lib/bundle/modes/plan.md`` (a ``mode:`` mapping with
    ``name`` / ``description`` / ``tools.safe`` / ``default_action``) so a frontmatter
    parse failure cannot masquerade as "the launch dir was not searched". It differs
    from plan.md in one respect only: ``default_action: allow`` rather than ``block``,
    matching the existing ``suites/modes/fixtures/e2e-probe-mode.md`` probe, since the
    probe imposes no tool policy and a blocking default would add noise to the turn.
    """
    dtu.push_file(
        dtu_id,
        str(FIXTURES / f"{PROBE_MODE}.md"),
        f"{WS_MODE}/.amplifier/modes/{PROBE_MODE}.md",
    )
    return WS_MODE


@pytest.fixture
def launchdir_skill(dtu_id: str) -> str:
    """Seed the probe skill into ``WS_SKILL/.amplifier/skills/`` and return WS_SKILL."""
    dtu.push_file(
        dtu_id,
        str(FIXTURES / PROBE_SKILL / "SKILL.md"),
        f"{WS_SKILL}/.amplifier/skills/{PROBE_SKILL}/SKILL.md",
    )
    return WS_SKILL
