"""End-to-end coverage for the ``gemini`` provider.

Proves the four things amplifier-agent became responsible for when gemini was
registered as the eighth provider: the credential report knows it, its models
list, a host config naming it validates, and a session runs on it.

The suite skips wholesale when GOOGLE_API_KEY is absent from the DTU. See
``conftest.py`` for why this differs from github_copilot's fail-loud guard.
"""

from __future__ import annotations

import pytest
from framework import harness
from framework.harness import E2ECase

from suites.gemini.cases import MODELS, SMOKE, WIRING

pytestmark = pytest.mark.dtu


def _ids(cases: list[E2ECase]) -> list[str]:
    return [c.name for c in cases]


@pytest.mark.parametrize("case", WIRING, ids=_ids(WIRING))
def test_gemini_wiring(case: E2ECase, dtu_id: str, gemini_config: str) -> None:
    """Catalog registration, credential variable, and config validation."""
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", MODELS, ids=_ids(MODELS))
def test_gemini_models_list(case: E2ECase, dtu_id: str, gemini_config: str) -> None:
    """A live model listing comes back non-empty and well-formed."""
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", SMOKE, ids=_ids(SMOKE))
def test_gemini_basic_reply(case: E2ECase, dtu_id: str, gemini_config: str) -> None:
    """One real session completes end to end against the provider module."""
    harness.run_cli_case(dtu_id, case)
