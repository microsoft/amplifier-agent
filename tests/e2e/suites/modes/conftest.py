"""Fixtures for the modes suite.

``seeded_mode`` pushes our test mode fixture into the running DTU at test time via
``dtu.push_file`` and returns the in-DTU launch directory the custom-mode case runs from.
``model_id`` resolves a served model id for the HTTP activeMode cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# In-DTU launch dir for the custom-mode case; the mode is discovered from its .amplifier/modes/.
WS_MODES = "/root/e2e/ws-modes"


@pytest.fixture
def seeded_mode(dtu_id: str) -> str:
    """Seed a custom mode in the launch directory ``WS_MODES/.amplifier/modes/`` and return WS_MODES."""
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "e2e-probe-mode.md"),
        f"{WS_MODES}/.amplifier/modes/e2e-probe-mode.md",
    )
    return WS_MODES


@pytest.fixture(scope="session")
def model_id(dtu_id: str, server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models (first entry).

    The served model depends on host-config, so we never hardcode it.
    """
    cmd = f"curl -s -H 'Authorization: Bearer {server['token']}' {server['base_url']}/v1/models"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"/v1/models failed: {result.get('stderr')}"
    data = json.loads(result["stdout"])
    models = data.get("data") or []
    assert models, f"no served models: {data}"
    return models[0]["id"]
