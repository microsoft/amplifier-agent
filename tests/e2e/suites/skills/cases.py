"""Case data for skills discovery.

Covers the ``skills list --json`` CLI command and the ``GET /v1/skills`` HTTP route,
which enumerate the user-invocable skills. This targets functionality that does not
exist yet, so the tests carry an ``xfail`` marker (see docs/E2E_TESTING.md).
"""

from __future__ import annotations

from framework.assertions import expect_set
from framework.harness import E2ECase

SKILLS: list[E2ECase] = [
    E2ECase("skills-list-cli", "cli", ["skills", "list", "--json"], check=expect_set({"code-review", "council"})),
    E2ECase("skills-list-http", "http", ("GET", "/v1/skills"), check=expect_set({"code-review", "council"})),
]

_CONFIG = "/root/e2e/host-config.json"
_CFG_SKILLS = "/root/e2e/host-config-skills.json"
_WS = "/root/e2e/ws-skills"

# Skill invocation via the `!amplifier:skill <name>` sigil on `run`. The sigil is not built
# yet, so these are xfail(strict=True) in test_skills.py. check=None = runs-clean baseline.
INVOCATIONS: list[E2ECase] = [
    # 1. Built-in shipped skill, invoked by its real name.
    E2ECase(
        "skill-invoke-builtin-code-review",
        "cli",
        ["run", "-y", "--config", _CONFIG, "!amplifier:skill code-review"],
        check=None,
    ),
    # 2. Custom skill auto-discovered from the launch directory (cwd=_WS).
    E2ECase(
        "skill-invoke-custom-launch-dir",
        "cli",
        ["run", "-y", "--config", _CONFIG, "!amplifier:skill e2e-crusty-probe"],
        check=None,
        cwd=_WS,
    ),
    # 3. Skill discovered via a configured host-config `skills.skills` location (not the launch dir).
    #    Also exercises the text-after-name path: everything after the skill name is the skill's
    #    $ARGUMENTS, passed verbatim.
    E2ECase(
        "skill-invoke-configured-location-with-args",
        "cli",
        ["run", "-y", "--config", _CFG_SKILLS, "!amplifier:skill e2e-configured-probe please look at the e2e tests"],
        check=None,
    ),
]
