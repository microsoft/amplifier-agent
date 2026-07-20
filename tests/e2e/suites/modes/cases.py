"""Case data for modes discovery.

Covers the ``modes list --json`` CLI command and the ``GET /v1/modes`` HTTP route,
which enumerate the shipped modes. This targets functionality that does not exist yet,
so the tests carry an ``xfail`` marker (see docs/E2E_TESTING.md).
"""

from __future__ import annotations

from framework.assertions import expect_set
from framework.harness import E2ECase

MODES: list[E2ECase] = [
    E2ECase("modes-list-cli", "cli", ["modes", "list", "--json"], check=expect_set({"plan", "brainstorm"})),
    E2ECase("modes-list-http", "http", ("GET", "/v1/modes"), check=expect_set({"plan", "brainstorm"})),
]
