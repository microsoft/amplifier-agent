"""Pytest wiring for the DTU-based e2e suite.

The whole suite attaches to a WARM DTU provisioned out-of-band by
``uv run python tests/e2e/framework/cli.py up`` (or auto-provisioned by ``cli.py run``).
Tests read the shared state file to find the instance rather than launching their
own, and self-skip cleanly whenever the DTU tooling or a warm instance is absent —
so a plain ``uv run pytest`` stays green on any host.
"""

from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

# Make the `framework` and `suites` packages importable (this file's own directory,
# tests/e2e/, is their shared parent).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from framework import dtu, state


def pytest_configure(config: pytest.Config) -> None:
    """Register the `dtu` marker (also declared in pyproject for strict-markers)."""
    config.addinivalue_line("markers", "dtu: DTU-based end-to-end tests requiring amplifier-digital-twin")


@pytest.fixture(scope="session", autouse=True)
def _require_dtu_cli() -> None:
    """Skip the entire e2e suite when the DTU CLI is not installed."""
    if shutil.which("amplifier-digital-twin") is None:
        pytest.skip("amplifier-digital-twin not on PATH", allow_module_level=True)


@pytest.fixture(scope="session")
def e2e_state() -> dict[str, Any]:
    """Return the warm-DTU state, skipping if none is provisioned or it is not ready."""
    current = state.read_state()
    if current is None:
        pytest.skip("no warm DTU; run `uv run python tests/e2e/framework/cli.py up`")
        raise RuntimeError("unreachable")  # help type-narrowing when skip isn't seen as NoReturn
    if not dtu.check_ready(current["dtu_id"]):
        pytest.skip(f"DTU {current['dtu_id']} not ready; re-run `up`")
    return current


@pytest.fixture(scope="session")
def dtu_id(e2e_state: dict[str, Any]) -> str:
    """The warm DTU instance id."""
    return e2e_state["dtu_id"]


@pytest.fixture(scope="session")
def server(dtu_id: str) -> Generator[dict[str, str], None, None]:
    """Start the amplifier-agent HTTP server INSIDE the DTU once for HTTP cases.

    Launches ``serve chat-completions`` bound to 0.0.0.0:9099 (so curl-from-inside
    on localhost works), then polls ``/v1/models`` until it answers 200 or ~60s pass.
    Yields the base_url + bearer token. Best-effort pkill on teardown.
    """
    base_url = "http://localhost:9099"
    token = "local-dev-secret"

    start = (
        "mkdir -p /root/e2e && "
        "nohup amplifier-agent serve chat-completions "
        "--bind 0.0.0.0 --port 9099 --api-key local-dev-secret "
        ">/root/e2e/serve.log 2>&1 &"
    )
    dtu.exec_json(dtu_id, ["bash", "-lc", start])

    probe = f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Authorization: Bearer {token}' {base_url}/v1/models"
    deadline = time.monotonic() + 60
    ready = False
    while time.monotonic() < deadline:
        result = dtu.exec_json(dtu_id, ["bash", "-lc", probe])
        if result.get("exit_code") == 0 and result.get("stdout", "").strip() == "200":
            ready = True
            break
        time.sleep(3)

    if not ready:
        log = dtu.exec_json(dtu_id, ["bash", "-lc", "cat /root/e2e/serve.log 2>/dev/null || true"])
        pytest.fail(f"amplifier-agent server did not become ready in the DTU\nserve.log:\n{log.get('stdout', '')}")

    try:
        yield {"base_url": base_url, "token": token}
    finally:
        dtu.exec_json(dtu_id, ["bash", "-lc", "pkill -f 'amplifier-agent serve' || true"])
