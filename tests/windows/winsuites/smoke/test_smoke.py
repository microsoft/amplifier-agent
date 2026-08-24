"""Smoke suite: does amplifier-agent install and run on Windows.

Keyless by design, so this suite runs anywhere a Windows engine exists. If
these fail, nothing else in the Windows story is worth debugging yet.
"""

from __future__ import annotations

import pytest
from winframework import harness
from winframework.harness import WinCase

from winsuites.smoke.cases import SMOKE

pytestmark = pytest.mark.windows


@pytest.mark.parametrize("case", SMOKE, ids=[c.name for c in SMOKE])
def test_smoke(case: WinCase, agent: str) -> None:
    harness.run_cli_case(agent, case)
