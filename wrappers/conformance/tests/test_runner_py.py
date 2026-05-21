"""Conformance runner tests — Python.

Tests that run_fixture() passes for the two required fixtures.
RED: fails because runner_py.py does not exist yet.
GREEN: passes once runner_py.py is implemented.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add the conformance directory to sys.path so we can import runner_py
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

FIXTURES_DIR = (
    Path(__file__).parent.parent.parent.parent / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
)


@pytest.mark.asyncio
async def test_capability_negotiation():
    from runner_py import run_fixture

    report = await run_fixture(FIXTURES_DIR / "capability_negotiation.yaml")
    assert report["passed"] is True, f"Expected passed=True, got: {report}"


@pytest.mark.asyncio
async def test_l14_synthesis():
    from runner_py import run_fixture

    report = await run_fixture(FIXTURES_DIR / "l14_synthesis.yaml")
    assert report["passed"] is True, f"Expected passed=True, got: {report}"
