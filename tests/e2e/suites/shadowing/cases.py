"""Case data for skill/mode name-collision (shadow) reporting.

Skills and modes are discovered first-match-wins across an ordered list of roots. The
loser used to vanish silently, so a user whose override was ignored had no way to find
out. Every listing entry now carries the winning file as ``source`` plus a ``shadowed``
list naming every same-named file that lost, on both the CLI (``skills list --json`` /
``modes list --json``) and HTTP (``GET /v1/skills`` / ``GET /v1/modes``) surfaces.

Precedence differs between the two, deliberately, and these cases pin both:

    skills   builtin bundle dir, $AMPLIFIER_SKILLS_DIR, <cwd>/.amplifier/skills,
             ~/.amplifier/skills, config skills dirs        -> the BUILT-IN wins
    modes    <cwd>/.amplifier/modes, ~/.amplifier/modes,
             builtin bundle dir                             -> the USER copy wins

The mode ordering mirrors the mode ACTIVATION path in hooks-mode, so what is listed is
what runs -- ``MODE_LISTING_MATCHES_ACTIVATION`` proves that end to end rather than
asserting it in a comment.

Paths, names, and the sentinel live in ``conftest.py`` next to the fixtures that seed
them, so a case and its seed can never drift apart.
"""

from __future__ import annotations

from framework.assertions import expect_contains, expect_no_shadows, expect_shadow
from framework.harness import E2ECase

from suites.shadowing.conftest import (
    BUILTIN_MODE,
    BUILTIN_MODES_MARKER,
    BUILTIN_SKILL,
    BUILTIN_SKILLS_MARKER,
    CONFIG,
    HOME_MODES,
    HOME_SKILLS,
    PLAN_SENTINEL,
    SHADOW_MODE,
    SHADOW_SKILL,
    WS_MODES,
    WS_PLAN,
    WS_SKILLS,
)

# 1. Two copies of one skill name, launch dir vs ~/.amplifier. The launch dir is searched
#    first, so it wins and the home copy is reported as shadowed. expect_shadow also
#    asserts exactly ONE entry carries the name -- collapsing the collision into a single
#    winner is half the contract.
SKILL_SHADOW_CLI = E2ECase(
    "skill-shadow-reported-cli",
    "cli",
    ["skills", "list", "--json"],
    check=expect_shadow(
        SHADOW_SKILL,
        source_contains=f"{WS_SKILLS}/.amplifier/skills/{SHADOW_SKILL}/SKILL.md",
        shadowed_contains=f"{HOME_SKILLS}/{SHADOW_SKILL}/SKILL.md",
    ),
    cwd=WS_SKILLS,
)

# 2. THE REGRESSION GUARD FOR SELF-SHADOWING, and the whole reason this case exists.
#    No cwd, so the command runs from the exec default (/root) -- where
#    `<cwd>/.amplifier/skills` and `~/.amplifier/skills` are THE SAME DIRECTORY. Discovery
#    collapses roots by resolved path; without that collapse the second pass over the same
#    directory would report every skill as shadowing itself, and every entry here would
#    carry a bogus non-empty `shadowed`. Nothing is seeded: a clean listing must report a
#    clean absence of collisions.
NO_SHADOW = E2ECase(
    "no-shadow-is-empty",
    "cli",
    ["skills", "list", "--json"],
    check=expect_no_shadows(),
)

# 3. The mode twin of case 1. NOTE: `modes list` has NO --config option, so this case
#    passes none; mode search paths are conventional, not config-driven.
MODE_SHADOW_CLI = E2ECase(
    "mode-shadow-reported-cli",
    "cli",
    ["modes", "list", "--json"],
    check=expect_shadow(
        SHADOW_MODE,
        source_contains=f"{WS_MODES}/.amplifier/modes/{SHADOW_MODE}.md",
        shadowed_contains=f"{HOME_MODES}/{SHADOW_MODE}.md",
    ),
    cwd=WS_MODES,
)

# 4. The same reporting over HTTP. The collision is seeded in ~/.amplifier/skills rather
#    than a launch dir precisely because a test cannot choose the server's working
#    directory -- a launch-dir collision would simply be invisible here. Runs against the
#    suite's own server (see the `shadow_server` fixture) because discovery is frozen at
#    server startup.
SKILL_SHADOW_HTTP = E2ECase(
    "skill-shadow-reported-http",
    "http",
    ("GET", "/v1/skills"),
    check=expect_shadow(
        BUILTIN_SKILL,
        source_contains=BUILTIN_SKILLS_MARKER,
        shadowed_contains=f"{HOME_SKILLS}/{BUILTIN_SKILL}/SKILL.md",
    ),
)

# 5. Documents the SURPRISING-BUT-INTENDED skills precedence: the vendored built-in root
#    is searched FIRST, so a user's same-named override in the launch dir LOSES. That is
#    the opposite of the modes ordering above, and the opposite of what most people expect
#    from a project-local override. It is intentional -- the built-in dir is also first on
#    the tool-skills invocation path, so listing and execution agree -- and this case is
#    here so any change to that ordering is a deliberate, visible decision.
BUILTIN_SHADOWS_USER_CLI = E2ECase(
    "builtin-shadows-user-cli",
    "cli",
    ["skills", "list", "--json"],
    check=expect_shadow(
        BUILTIN_SKILL,
        source_contains=BUILTIN_SKILLS_MARKER,
        shadowed_contains=f"{WS_SKILLS}/.amplifier/skills/{BUILTIN_SKILL}/SKILL.md",
    ),
    cwd=WS_SKILLS,
)

# 6. The guard against LISTING and ACTIVATION drifting apart again. Two ordered CLI steps
#    sharing one launch dir:
#
#      (a) `modes list --json` reports the seeded launch-dir plan.md as the winner and the
#          built-in as shadowed.
#      (b) `run --mode plan` emits PLAN_SENTINEL, a token that exists ONLY inside that
#          seeded file, proving the file the listing NAMED is the file that RAN.
#
#    Deliberately NOT a `cli-multi` case: `run_multi_case` ignores `E2ECase.cwd` (it execs
#    argv directly rather than through `bash -lc 'cd ...'`), and both steps here depend
#    entirely on the launch directory. Two `cli` cases run in order by one test is the
#    shape the harness actually supports.
MODE_LISTING_MATCHES_ACTIVATION: list[E2ECase] = [
    E2ECase(
        "mode-listing-matches-activation-list",
        "cli",
        ["modes", "list", "--json"],
        check=expect_shadow(
            BUILTIN_MODE,
            source_contains=f"{WS_PLAN}/.amplifier/modes/{BUILTIN_MODE}.md",
            shadowed_contains=BUILTIN_MODES_MARKER,
        ),
        cwd=WS_PLAN,
    ),
    E2ECase(
        "mode-listing-matches-activation-run",
        "cli",
        ["run", "-y", "--config", CONFIG, "--mode", BUILTIN_MODE, "Please acknowledge this request."],
        check=expect_contains(PLAN_SENTINEL),
        cwd=WS_PLAN,
    ),
]
