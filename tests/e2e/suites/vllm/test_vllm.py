"""End-to-end coverage for the ``vllm`` provider.

Proves the four things amplifier-agent became responsible for when vllm was
registered as the ninth provider: the credential report knows it and resolves its
endpoint from the environment, its models list off that endpoint, a host config
naming it validates, and a session runs on it.

The suite skips wholesale when VLLM_BASE_URL is absent from the DTU, and also when
it is present but the server does not answer. See ``conftest.py`` for why an
unreachable endpoint is a skip rather than a failure.
"""

from __future__ import annotations

import pytest
from framework import harness
from framework.harness import E2ECase

from suites.vllm.cases import MODELS, SMOKE, WIRING

pytestmark = pytest.mark.dtu


def _ids(cases: list[E2ECase]) -> list[str]:
    return [c.name for c in cases]


@pytest.mark.parametrize("case", WIRING, ids=_ids(WIRING))
def test_vllm_wiring(case: E2ECase, dtu_id: str, vllm_config: str) -> None:
    """Catalog registration, endpoint resolution, and config validation."""
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", MODELS, ids=_ids(MODELS))
def test_vllm_models_list(case: E2ECase, dtu_id: str, vllm_config: str) -> None:
    """A live model listing off the configured server comes back non-empty."""
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", SMOKE, ids=_ids(SMOKE))
def test_vllm_basic_reply(case: E2ECase, dtu_id: str, vllm_config: str) -> None:
    """One real session completes end to end against the vLLM server."""
    harness.run_cli_case(dtu_id, case)
