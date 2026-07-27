"""DTU-backed tests for skill/mode name-collision (shadow) reporting.

Each case needs its own seeded collision, so these are individual test functions rather
than one parametrized sweep: a shared fixture set would seed collisions that some cases
must NOT see (``no-shadow-is-empty`` in particular asserts an absence). The case data
still lives in ``cases.py``; these functions only wire cases to fixtures and runners.

See ``cases.py`` for what each case pins and ``conftest.py`` for how the collisions are
seeded and cleaned up.
"""

from __future__ import annotations

import pytest
from framework import harness

from suites.shadowing.cases import (
    BUILTIN_SHADOWS_USER_CLI,
    MODE_LISTING_MATCHES_ACTIVATION,
    MODE_SHADOW_CLI,
    NO_SHADOW,
    SKILL_SHADOW_CLI,
    SKILL_SHADOW_HTTP,
)

pytestmark = pytest.mark.dtu


def test_skill_shadow_reported_cli(dtu_id: str, shadowed_skill: str) -> None:
    """A skill seeded in both the launch dir and ~/.amplifier reports one winner + one loser."""
    harness.run_cli_case(dtu_id, SKILL_SHADOW_CLI)


def test_no_shadow_is_empty(dtu_id: str) -> None:
    """With nothing seeded, EVERY entry must report ``shadowed == []``.

    Runs from the exec default (/root), where ``<cwd>/.amplifier/skills`` and
    ``~/.amplifier/skills`` are the same directory. This is the regression guard for
    self-shadowing and is the whole reason the case exists.
    """
    harness.run_cli_case(dtu_id, NO_SHADOW)


def test_mode_shadow_reported_cli(dtu_id: str, shadowed_mode: str) -> None:
    """A mode seeded in both the launch dir and ~/.amplifier reports one winner + one loser."""
    harness.run_cli_case(dtu_id, MODE_SHADOW_CLI)


def test_skill_shadow_reported_http(dtu_id: str, shadow_server: dict[str, str]) -> None:
    """The same shadow fields survive the HTTP surface unchanged.

    Runs against the suite's OWN server (``shadow_server``), which is started only after
    the collision is on disk -- ``GET /v1/skills`` serves a listing frozen at server
    startup, so seeding after the shared session server booted would test nothing.
    """
    harness.run_http_case(shadow_server["base_url"], shadow_server["token"], dtu_id, SKILL_SHADOW_HTTP)


def test_builtin_shadows_user_cli(dtu_id: str, shadowed_builtin_skill_ws: str) -> None:
    """For SKILLS the built-in wins and the user's launch-dir override loses."""
    harness.run_cli_case(dtu_id, BUILTIN_SHADOWS_USER_CLI)


def test_mode_listing_matches_activation(dtu_id: str, shadowed_plan_mode: str) -> None:
    """The mode a listing names as the winner must be the mode that actually RUNS.

    One test, two ordered steps, deliberately not split: neither half means anything
    alone. Listing the seeded file proves nothing about activation, and emitting the
    sentinel proves nothing about what the listing claimed. Asserted together, in this
    order, they pin listing and activation to the same file -- which is the drift this
    case exists to catch.
    """
    for case in MODE_LISTING_MATCHES_ACTIVATION:
        harness.run_cli_case(dtu_id, case)
