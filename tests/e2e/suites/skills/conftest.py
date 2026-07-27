"""Fixtures for the skills invocation suite: seed skill files into the DTU.

These push our test skill fixtures into the running DTU at test time via
``dtu.push_file`` and return the in-DTU paths the cases launch against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# In-DTU paths.
WS = "/root/e2e/ws-skills"  # launch dir for the custom-skill case
EXTRA_SKILLS_DIR = "/root/e2e/extra-skills"  # configured (non-launch) skills location
CFG_SKILLS = "/root/e2e/host-config-skills.json"

# Sentinel probe (test_sigil_dispatch.py). The token lives ONLY inside the probe's
# SKILL.md body, so its appearance at SIGIL_SENTINEL_PATH proves the body actually
# reached the model rather than the prompt merely being echoed.
SIGIL_PROBE_NAME = "e2e-sigil-probe"
SIGIL_SENTINEL = "SIGIL-DISPATCH-OK-K7R2"
SIGIL_SENTINEL_PATH = "/root/e2e/sigil_probe_ran.txt"

# Memory probe (test_skill_body_persistence.py). A two-rule inline skill: rule 1 says
# "answer THIS turn with MEMORY_PROBE_ARMED and do not write the token", rule 2 says
# "when later asked for the probe token, answer with it verbatim". Rule 2 can only be
# honored if the skill BODY is still in scope on a later turn, which is exactly the
# property under test.
#
# The token must NOT appear in the turn-1 reply, and the SKILL.md body says so
# explicitly. If the arming reply echoed the token, that reply would enter turn-2
# history as ordinary assistant text, and the model could recite the token on turn 2
# from history alone -- with the skill body long gone. The test would then go green
# while the bug it exists to catch was fully intact. So both faces assert the token's
# ABSENCE on turn 1 as a confound guard before asserting its presence on turn 2.
#
# The token string appears nowhere else in this repository, so a turn-2 hit cannot be
# explained by the model having picked it up from any other context.
MEMORY_PROBE_NAME = "e2e-memory-probe"
MEMORY_PROBE_TOKEN = "MEMORY-PROBE-TOKEN-J4X8"
MEMORY_PROBE_ARMED = "MEMORY-PROBE-ARMED"

# Family of the model host-config.json selects for the CLI cases ("claude-sonnet-5").
# The HTTP cases prefer a served model matching this so both faces run the same
# model capability and "the HTTP model was weaker" cannot explain a difference.
_CLI_MODEL_HINT = "sonnet"


@pytest.fixture(scope="session")
def model_id(dtu_id: str, server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models.

    ``model`` is required on the request body (omitting it is a 422) and the
    served set depends on host-config, so we never hardcode it. Prefers a model
    matching ``_CLI_MODEL_HINT`` so both faces run the same model.
    """
    cmd = f"curl -s -H 'Authorization: Bearer {server['token']}' {server['base_url']}/v1/models"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"/v1/models failed: {result.get('stderr')}"
    data = json.loads(result["stdout"])
    models = data.get("data") or []
    assert models, f"no served models: {data}"

    ids = [m["id"] for m in models]
    for candidate in ids:
        if _CLI_MODEL_HINT in candidate:
            return candidate
    return ids[0]


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


@pytest.fixture
def memory_probe(dtu_id: str) -> str:
    """Seed the memory probe at ONE path both faces discover. Returns that in-DTU path.

    The destination is ``/root/.amplifier/skills/e2e-memory-probe/SKILL.md``, and one
    path is enough because ``/root`` does double duty:

      * The HTTP server runs with cwd=/root (it is launched by the session-scoped
        ``server`` fixture via ``bash -lc``), so ``/root/.amplifier/skills`` IS its
        launch-directory skills root -- entry 2 of ``_default_skill_dirs()``.
      * ``/root`` is also HOME in the DTU, so the same directory is the user skills
        dir ``~/.amplifier/skills`` -- entry 3 of ``_default_skill_dirs()`` -- which
        is what the CLI turns resolve, whatever directory they launch from.

    That is why neither face in test_skill_body_persistence.py needs a ``cwd``: there
    is no launch-directory juggling to do. It also removes "the skill was not found"
    as a competing explanation for a red, since a red there would have to be a red on
    BOTH faces, and the CLI control proves it is not.
    """
    dtu.push_file(
        dtu_id,
        str(FIXTURES / MEMORY_PROBE_NAME / "SKILL.md"),
        f"/root/.amplifier/skills/{MEMORY_PROBE_NAME}/SKILL.md",
    )
    return f"/root/.amplifier/skills/{MEMORY_PROBE_NAME}/SKILL.md"
