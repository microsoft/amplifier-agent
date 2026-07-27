"""DTU-backed tests for modes discovery.

The ``modes list`` command and ``/v1/modes`` route are implemented; these tests
exercise them live in a DTU (see docs/E2E_TESTING.md).
"""

from __future__ import annotations

import json

import pytest
from framework import harness
from framework.assertions import names
from framework.harness import E2ECase

from suites.modes.cases import ACTIVATIONS, CUSTOM, MODES

pytestmark = pytest.mark.dtu


def _run_case(dtu_id: str, server: dict[str, str], case: E2ECase) -> None:
    """Dispatch a case to the cli or http runner based on its kind."""
    if case.kind == "cli":
        harness.run_cli_case(dtu_id, case)
    else:
        harness.run_http_case(server["base_url"], server["token"], dtu_id, case)


@pytest.mark.parametrize("case", MODES, ids=[c.name for c in MODES])
def test_modes_discovery(case: E2ECase, dtu_id: str, server: dict[str, str]) -> None:
    _run_case(dtu_id, server, case)


def test_modes_parity(dtu_id: str, server: dict[str, str]) -> None:
    """cli and http mode name sets must match."""
    cli_case = next(c for c in MODES if c.kind == "cli")
    http_case = next(c for c in MODES if c.kind == "http")
    assert isinstance(cli_case.command, list)
    assert isinstance(http_case.command, tuple)

    cli_result = harness.dtu.exec_json(dtu_id, ["amplifier-agent", *cli_case.command])
    assert cli_result.get("exit_code") == 0, (
        f"[{cli_case.name}] expected exit 0, got {cli_result.get('exit_code')}\nstderr:\n{cli_result.get('stderr', '')}"
    )
    cli_names = names(json.loads(cli_result["stdout"]))

    _method, path = http_case.command
    curl = f"curl -s -w '\\n%{{http_code}}' -H 'Authorization: Bearer {server['token']}' {server['base_url']}{path}"
    http_result = harness.dtu.exec_json(dtu_id, ["bash", "-lc", curl])
    http_body, _, http_status = http_result.get("stdout", "").rpartition("\n")
    assert http_status.strip() == "200", (
        f"[{http_case.name}] expected HTTP 200, got {http_status.strip()!r}\nbody:\n{http_body}"
    )
    http_names = names(json.loads(http_body))
    assert cli_names == http_names, f"modes cli {sorted(cli_names)} != http {sorted(http_names)}"


@pytest.mark.parametrize("case", ACTIVATIONS, ids=[c.name for c in ACTIVATIONS])
def test_mode_activation(case: E2ECase, dtu_id: str) -> None:
    """Set a pre-baked mode via --mode, persist it across a resume by re-passing, and disable it by
    omitting --mode. Each turn asserts metadata.activeMode. CLI-only, so only dtu_id is used."""
    harness.run_multi_case(dtu_id, case)


@pytest.mark.parametrize("case", CUSTOM, ids=[c.name for c in CUSTOM])
def test_mode_custom(case: E2ECase, dtu_id: str, seeded_mode: str) -> None:
    """Activate a custom mode discovered from the launch dir's .amplifier/modes/ (seeded first)."""
    harness.run_cli_case(dtu_id, case)
