"""Case data for the launch-directory contract.

The contract, in one sentence:

    When ``--cwd`` is NOT passed, the directory the command was LAUNCHED FROM is the
    working directory, and both modes and skills are discovered from it.

Three cases, and the value is entirely in reading them as a set:

    1. ``launch-dir-mode-body-activates``                    mode,  default --cwd
    2. ``launch-dir-mode-body-activates-with-explicit-cwd``   mode,  explicit --cwd  (CONTROL)
    3. ``launch-dir-skill-body-runs``                         skill, default --cwd  (GUARD)

Cases 1 and 2 are byte-for-byte identical apart from the ``--cwd`` flag: same seeded
mode file, same launch directory, same prompt, same sentinel. So the pair localizes any
failure to the DEFAULT for ``--cwd`` and nothing else.

Each assertion is a sentinel token that exists in exactly ONE fixture file, so a hit
proves that file's body reached the model. Paths, names, and sentinels live in
``conftest.py`` next to the fixtures that seed them, so a case and its seed cannot drift
apart (same arrangement as ``suites/shadowing/``).
"""

from __future__ import annotations

from framework.assertions import expect_contains
from framework.harness import E2ECase

from suites.launch_dir.conftest import (
    CONFIG,
    MODE_SENTINEL,
    PROBE_MODE,
    PROBE_SKILL,
    SKILL_SENTINEL,
    WS_MODE,
    WS_SKILL,
)

# The prompt is deliberately bland and carries no instruction of its own. The seeded mode
# body is what tells the model to emit the sentinel, so the sentinel can only come from
# the mode body having been injected.
_MODE_PROMPT = "Please acknowledge this request."


# 1. THE CONTRACT. `run` is launched FROM the seeded workspace and passes NO --cwd, so the
#    launch dir must be the working directory and `<launch dir>/.amplifier/modes` must be
#    searched during activation. The sentinel proves the seeded mode BODY was injected --
#    not merely that the name was accepted. `--mode` validation resolves through a
#    different code path than activation does, so a run can be accepted, report the mode
#    as active, and still never inject its body; asserting on the body is the only way to
#    tell those apart.
MODE_DEFAULT_CWD = E2ECase(
    "launch-dir-mode-body-activates",
    "cli",
    ["run", "-y", "--config", CONFIG, "--mode", PROBE_MODE, _MODE_PROMPT],
    check=expect_contains(MODE_SENTINEL),
    cwd=WS_MODE,
)

# 2. THE CONTROL. Identical to case 1 in every respect except that --cwd names the launch
#    directory explicitly. Its only job is to isolate the defect: when case 1 fails and
#    THIS case passes, the failure is "the DEFAULT for --cwd is wrong", NOT "mode bodies
#    never activate". Without it, a red on case 1 would be ambiguous between those two very
#    different diagnoses.
MODE_EXPLICIT_CWD = E2ECase(
    "launch-dir-mode-body-activates-with-explicit-cwd",
    "cli",
    ["run", "-y", "--config", CONFIG, "--cwd", WS_MODE, "--mode", PROBE_MODE, _MODE_PROMPT],
    check=expect_contains(MODE_SENTINEL),
    cwd=WS_MODE,
)

# 3. THE REGRESSION GUARD for the other half of the contract. Skill discovery is believed
#    to resolve the launch dir from the PROCESS cwd rather than from the session
#    working_dir capability that modes read, which would make it immune to the mode
#    defect -- but "believed" is not "pinned", so this case proves it instead of assuming
#    it, and keeps proving it once the --cwd default is fixed.
#
#    It also closes a real gap. `skill-invoke-custom-launch-dir` in `suites/skills/cases.py`
#    invokes a launch-dir skill with `check=None`, which asserts only that the command
#    exited 0 -- a run whose skill was never found also exits 0. This case asserts a
#    sentinel that lives only in the skill BODY, so it proves the body actually ran.
SKILL_DEFAULT_CWD = E2ECase(
    "launch-dir-skill-body-runs",
    "cli",
    ["run", "-y", "--config", CONFIG, f"!amplifier:skill {PROBE_SKILL}"],
    check=expect_contains(SKILL_SENTINEL),
    cwd=WS_SKILL,
)
