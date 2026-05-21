"""Phase 2.2 + 2.3 + 2.5 exit gate — end-to-end integration tests.

Tests the Python and TypeScript wrappers against a real ``amplifier-agent``
subprocess, and verifies cross-language conformance parity.

Binary skip:  tests skip automatically when ``amplifier-agent`` is not on PATH.
E2E skip:     wrapper integration tests skip unless ``AMPLIFIER_AGENT_E2E`` is
              set to a non-empty value.  This guard exists because real turns
              require a live provider (API key) that is unavailable in most CI
              environments.

Environment variables
---------------------
AMPLIFIER_AGENT_E2E
    Set to any non-empty value to enable the real-engine wrapper tests
    (``test_py_wrapper_drives_real_engine`` and
    ``test_ts_wrapper_drives_real_engine``).  When unset the tests are
    automatically skipped; they do **not** skip when a real run fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TS_CONFORMANCE_DIR = _REPO_ROOT / "wrappers" / "conformance"

# Availability checks evaluated once at collection time.
_has_binary: bool = shutil.which("amplifier-agent") is not None
_has_e2e: bool = bool(os.environ.get("AMPLIFIER_AGENT_E2E", ""))

# ---------------------------------------------------------------------------
# Test 1 — Python wrapper drives real engine
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_binary, reason="amplifier-agent not on PATH")
@pytest.mark.skipif(
    not _has_e2e,
    reason="AMPLIFIER_AGENT_E2E env var not set; set to any non-empty value to enable real-engine tests",
)
async def test_py_wrapper_drives_real_engine() -> None:
    """Py wrapper: spawn_agent against real subprocess, drain submit(), assert result/final.

    Spawns a real ``amplifier-agent`` subprocess via ``spawn_agent()``, submits
    the sentinel prompt ``'say hi'``, drains up to 50 events, disposes the
    session, and asserts that at least one event with ``type == 'result/final'``
    was received.
    """
    from amplifier_agent_client import spawn_agent  # type: ignore[import]
    from amplifier_agent_client.session import DisplayEvent  # type: ignore[import]

    session = await spawn_agent(
        lifecycle="one-shot",
        session_id="phase-2-2-gate",
    )

    events: list[DisplayEvent] = []
    try:
        async for ev in session.submit("say hi"):
            events.append(ev)
            if len(events) >= 50:
                break
    finally:
        await session.dispose()

    assert any(ev.type == "result/final" for ev in events), (
        f"No 'result/final' event received in {len(events)} events: " + str([ev.type for ev in events])
    )


# ---------------------------------------------------------------------------
# Test 2 — TypeScript wrapper drives real engine
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _has_binary, reason="amplifier-agent not on PATH")
@pytest.mark.skipif(
    not _has_e2e,
    reason="AMPLIFIER_AGENT_E2E env var not set; set to any non-empty value to enable real-engine tests",
)
def test_ts_wrapper_drives_real_engine() -> None:
    """TS wrapper: exit_gate_driver.ts against real subprocess, assert sawResultFinal.

    Shells out to ``pnpm exec tsx exit_gate_driver.ts`` from
    ``wrappers/conformance/``, asserts the process exits 0, then parses the
    JSON report from stdout and asserts ``sawResultFinal == True``.
    """
    result = subprocess.run(
        ["pnpm", "exec", "tsx", "exit_gate_driver.ts"],
        cwd=str(_TS_CONFORMANCE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"exit_gate_driver.ts exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    report = json.loads(result.stdout.strip())
    assert report["sawResultFinal"] is True, f"sawResultFinal was not True in driver report: {report}"


# ---------------------------------------------------------------------------
# Test 3 — Conformance parity lint passes
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_conformance_parity_lint_passes() -> None:
    """Cross-language parity lint: TS and Py conformance runners agree on all fixtures.

    Invokes ``uv run pytest tests/test_conformance_parity.py -m integration -q``
    as a subprocess from the repo root and asserts the process exits 0.

    This test does **not** require a live provider; the conformance runners use
    scripted (replay) transports only.
    """
    result = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/test_conformance_parity.py",
            "-m",
            "integration",
            "-q",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, (
        f"Conformance parity lint failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
