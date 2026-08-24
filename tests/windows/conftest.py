"""Pytest wiring for the Windows-container e2e suite.

The suite attaches to an IMAGE built out of band by
``uv run tests/windows/winframework/cli.py up``. Each suite gets its own
container from that image, so suites cannot contaminate each other, while the
expensive part (installing git, uv, python and amplifier-agent) is paid once
per image build rather than once per test.

Everything self-skips when the Windows engine or the image is absent, so a
plain ``uv run pytest`` stays green on any host, including Linux CI.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest

# Make the `winframework` and `winsuites` packages importable (this file's
# directory, tests/windows/, is their shared parent).
#
# The `win` prefix is load-bearing, not decoration. tests/e2e/ already owns
# top-level packages named `framework` and `suites` via the same sys.path
# trick. Since sys.modules is global, a bare `pytest tests/` that collects both
# trees would resolve `framework` to whichever tree was imported first and fail
# the other. Distinct names keep the two suites genuinely independent.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from winframework import container


def pytest_configure(config: pytest.Config) -> None:
    """Register the `windows` marker (also declared in pyproject for strict-markers)."""
    config.addinivalue_line(
        "markers",
        "windows: Windows-container end-to-end tests requiring a Windows Docker engine",
    )


@pytest.fixture(scope="session", autouse=True)
def _require_windows_engine() -> None:
    """Skip the whole suite when the Windows container engine is unreachable."""
    problems = container.preflight()
    if problems:
        pytest.skip("windows container engine unavailable: " + "; ".join(problems))


@pytest.fixture(scope="session")
def win_image(_require_windows_engine: None) -> str:
    """The provisioned image. Skips when it has not been built."""
    if not container.image_exists():
        pytest.skip(f"image {container.IMAGE} not built; run `uv run tests/windows/winframework/cli.py up`")
    return container.IMAGE


@pytest.fixture(scope="session")
def _suite_containers(win_image: str) -> Generator[dict[str, str], None, None]:
    """Containers started during this run, keyed by suite, removed at the end."""
    started: dict[str, str] = {}
    try:
        yield started
    finally:
        for name in started.values():
            container.remove(name)


@pytest.fixture
def agent(request: pytest.FixtureRequest, win_image: str, _suite_containers: dict[str, str]) -> str:
    """The container for the calling test's suite, created on first use.

    Deliberately NOT pytest's `package` scope. A package-scoped fixture defined
    in a root conftest resolves to the session node, which yields ONE container
    for the whole run rather than one per suite. Keying an explicit dict on the
    suite directory is what actually delivers per-suite granularity.

    Per suite rather than per test: container startup is a few seconds, and
    paying it per case would make the suite heavy for little gain. Suites stay
    isolated from each other, which is the boundary that matters here.
    """
    suite = request.path.parent.name
    if suite not in _suite_containers:
        name = f"aa-win-e2e-{suite}-{uuid4().hex[:6]}"
        container.start(name, win_image)
        _suite_containers[suite] = name
    return _suite_containers[suite]


@pytest.fixture(scope="session")
def anthropic_key() -> str:
    """Skip a suite that needs a live model when no key is on the host.

    Skip rather than fail: an absent key is a fact about the operator's
    machine, not a defect in Windows support. The keyless smoke suite still
    proves the install and the CLI work.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set on the host")
    return key
