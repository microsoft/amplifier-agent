"""DTU-backed tests for the launch-directory contract.

    When ``--cwd`` is NOT passed, the directory the command was LAUNCHED FROM is the
    working directory, and both modes and skills are discovered from it.

Individual test functions rather than one parametrized sweep, because each case needs a
different seed and the mode pair only means anything when read together with its control
(same shape as ``suites/shadowing/test_shadowing.py``).

See ``cases.py`` for what each case pins and ``conftest.py`` for how the probes are
seeded.
"""

from __future__ import annotations

import pytest
from framework import harness

from suites.launch_dir.cases import MODE_DEFAULT_CWD, MODE_EXPLICIT_CWD, SKILL_DEFAULT_CWD

pytestmark = pytest.mark.dtu


def test_launch_dir_mode_body_activates(dtu_id: str, launchdir_mode: str) -> None:
    """A mode seeded in the LAUNCH dir must have its body injected with no ``--cwd`` passed.

    The sentinel is the assertion, and it is the only honest one available: ``--mode`` is
    validated through a code path that does resolve the launch directory, so a run can be
    accepted and report the mode as active while its body was never injected. Asserting on
    the BODY is what separates "the mode ran" from "the mode name was accepted".
    """
    harness.run_cli_case(dtu_id, MODE_DEFAULT_CWD)


def test_launch_dir_mode_body_activates_with_explicit_cwd(dtu_id: str, launchdir_mode: str) -> None:
    """CONTROL for the case above: same mode, same launch dir, ``--cwd`` passed explicitly.

    This exists to make a failure of ``test_launch_dir_mode_body_activates`` unambiguous.
    The two cases differ by the ``--cwd`` flag and nothing else, so this one passing while
    that one fails isolates the defect to "the DEFAULT for ``--cwd``" rather than "mode
    bodies never activate". If BOTH fail, the problem is mode activation itself and the
    launch-directory reading is wrong.
    """
    harness.run_cli_case(dtu_id, MODE_EXPLICIT_CWD)


def test_launch_dir_skill_body_runs(dtu_id: str, launchdir_skill: str) -> None:
    """A skill seeded in the LAUNCH dir must actually RUN with no ``--cwd`` passed.

    The regression guard for the other half of the contract: skill discovery must keep
    working once the ``--cwd`` default changes. It also closes a gap in the existing
    coverage -- ``skill-invoke-custom-launch-dir`` in ``suites/skills/cases.py`` uses
    ``check=None``, so it only proves the command exited 0, which a run that never found
    the skill also does. This case asserts a sentinel that exists only in the skill body.
    """
    harness.run_cli_case(dtu_id, SKILL_DEFAULT_CWD)
