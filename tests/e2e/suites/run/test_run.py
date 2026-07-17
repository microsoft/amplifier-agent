"""DTU-backed tests for the ``amplifier-agent run`` command.

``run-basic-reply`` checks a single-shot model round-trip; ``run-resume-session``
checks that state carries across a ``--resume`` within one session. CLI-only — no HTTP
server needed, so only the ``dtu_id`` fixture is used.
"""

from __future__ import annotations

import pytest
from framework import harness
from framework.harness import E2ECase

from suites.run.cases import BASIC, RESUME

pytestmark = pytest.mark.dtu


@pytest.mark.parametrize("case", BASIC, ids=[c.name for c in BASIC])
def test_run_basic(case: E2ECase, dtu_id: str) -> None:
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", RESUME, ids=[c.name for c in RESUME])
def test_run_resume(case: E2ECase, dtu_id: str) -> None:
    harness.run_multi_case(dtu_id, case)
