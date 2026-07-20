"""Case data for skills discovery.

Covers the ``skills list --json`` CLI command and the ``GET /v1/skills`` HTTP route,
which enumerate the user-invocable skills. This targets functionality that does not
exist yet, so the tests carry an ``xfail`` marker (see docs/E2E_TESTING.md).
"""

from __future__ import annotations

from framework.assertions import expect_set
from framework.harness import E2ECase

SKILLS: list[E2ECase] = [
    E2ECase("skills-list-cli", "cli", ["skills", "list", "--json"], check=expect_set({"code-review", "council"})),
    E2ECase("skills-list-http", "http", ("GET", "/v1/skills"), check=expect_set({"code-review", "council"})),
]
