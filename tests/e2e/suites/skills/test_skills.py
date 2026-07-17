"""DTU-backed tests for skills discovery.

The ``skills list`` command and ``/v1/skills`` route do not exist yet, so these carry
``xfail(strict=True)``: each case still runs and must fail, and surfaces as a hard
failure once the feature lands, signalling that the marker should be removed (see
docs/E2E_TESTING.md).
"""

from __future__ import annotations

import json

import pytest
from framework import harness
from framework.assertions import names
from framework.harness import E2ECase

from suites.skills.cases import SKILLS

pytestmark = pytest.mark.dtu


def _run_case(dtu_id: str, server: dict[str, str], case: E2ECase) -> None:
    """Dispatch a case to the cli or http runner based on its kind."""
    if case.kind == "cli":
        harness.run_cli_case(dtu_id, case)
    else:
        harness.run_http_case(server["base_url"], server["token"], dtu_id, case)


@pytest.mark.xfail(reason="skills feature not built yet", strict=True)
@pytest.mark.parametrize("case", SKILLS, ids=[c.name for c in SKILLS])
def test_skills_discovery(case: E2ECase, dtu_id: str, server: dict[str, str]) -> None:
    _run_case(dtu_id, server, case)


@pytest.mark.xfail(reason="skills feature not built yet", strict=True)
def test_skills_parity(dtu_id: str, server: dict[str, str]) -> None:
    """cli and http skill name sets must match once implemented."""
    cli_case = next(c for c in SKILLS if c.kind == "cli")
    http_case = next(c for c in SKILLS if c.kind == "http")
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
    assert cli_names == http_names, f"skills cli {sorted(cli_names)} != http {sorted(http_names)}"
