"""Fixtures for the shadowing suite: seed COLLIDING skill/mode files into the DTU.

Every case here needs two files with the same name under different discovery roots, so
each fixture pushes a pair (or a single override that collides with a vendored built-in)
via ``dtu.push_file`` and returns the in-DTU launch directory the case runs from.

Two rules this module follows deliberately:

* **Anything written under ``~/.amplifier`` is removed on teardown.** Not tidiness --
  ``no-shadow-is-empty`` asserts that a listing taken from the default exec dir reports
  NO shadowing at all, and a leftover home-directory copy of a built-in would make it
  fail. The home dir is also the HTTP server's launch dir and is shared with every other
  suite, so a leak here corrupts tests that never opted into a collision.
* **The HTTP case gets its OWN server.** Skills/modes discovery is frozen at server
  startup (``app.py`` lifespan populates ``app.state.available_skills`` once), and the
  session-scoped ``server`` fixture in ``tests/e2e/conftest.py`` is typically already
  running by the time this suite executes (the ``modes`` suite requests it first). There
  is no fixture ordering that can retroactively put our seed before that startup, so
  ``shadow_server`` seeds first and then starts a SECOND server on its own port. It is
  function-scoped so the seed exists only for the duration of that one test.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from framework import dtu, ports

FIXTURES = Path(__file__).parent / "fixtures"

# --------------------------------------------------------------------------- #
# In-DTU paths and names
# --------------------------------------------------------------------------- #

# Host-config seeded into every DTU by provisioning (anthropic provider, approval "yes").
CONFIG = "/root/e2e/host-config.json"

# Launch dirs. One per collision so a case's seeds can never be confused for another's.
WS_SKILLS = "/root/e2e/ws-shadow-skills"  # e2e-shadow-probe + a code-review override
WS_MODES = "/root/e2e/ws-shadow-modes"  # e2e-shadow-mode
WS_PLAN = "/root/e2e/ws-shadow-plan"  # a plan override carrying the sentinel

# The user-level roots. These are also ``<cwd>/.amplifier/...`` for any process launched
# from /root (the exec default and the HTTP server's cwd), which is exactly the
# self-shadowing situation ``no-shadow-is-empty`` guards.
HOME_SKILLS = "/root/.amplifier/skills"
HOME_MODES = "/root/.amplifier/modes"

# Substrings identifying the VENDORED built-in roots. Derived from the package layout
# (``amplifier_agent_lib.bundle`` ships skills/ and modes/), so they hold wherever uv
# installed the tool.
BUILTIN_SKILLS_MARKER = "amplifier_agent_lib/bundle/skills"
BUILTIN_MODES_MARKER = "amplifier_agent_lib/bundle/modes"

# Names under test.
SHADOW_SKILL = "e2e-shadow-probe"
SHADOW_MODE = "e2e-shadow-mode"
BUILTIN_SKILL = "code-review"  # collides with a vendored built-in skill
BUILTIN_MODE = "plan"  # collides with a vendored built-in mode

# Lives ONLY in fixtures/plan-ws.md. Seeing it in a turn's output proves that specific
# file supplied the mode instructions. Same technique as SIGIL_SENTINEL in suites/skills/.
PLAN_SENTINEL = "SHADOW-PLAN-OVERRIDE-Q4V9"

# --------------------------------------------------------------------------- #
# Suite-local HTTP server (see the module docstring for why it exists)
# --------------------------------------------------------------------------- #

# Owned by this suite. Every e2e port is declared in framework/ports.py, which also
# explains why no two suites may share one: these servers are held by long-lived
# fixtures, so a duplicate binds-and-fails whenever both suites run in one session.
SHADOW_PORT = ports.SHADOWING_PORT
SHADOW_TOKEN = "shadow-e2e-secret"
SHADOW_BASE_URL = f"http://localhost:{SHADOW_PORT}"
SHADOW_LOG = "/root/e2e/serve-shadow.log"

# Match our server by port so the other suites' servers are never collateral, and so the
# pkill cannot match ITSELF -- see ports.self_safe_pkill for the bracketing trick.
_SHADOW_PKILL = ports.self_safe_pkill(SHADOW_PORT)


def _rm(dtu_id: str, path: str) -> None:
    """Delete a seeded path inside the DTU (best effort, used only on teardown)."""
    dtu.exec_json(dtu_id, ["bash", "-lc", f"rm -rf {shlex.quote(path)}"])


# --------------------------------------------------------------------------- #
# Skill collisions
# --------------------------------------------------------------------------- #


@pytest.fixture
def shadowed_skill(dtu_id: str) -> Generator[str, None, None]:
    """Seed the SAME skill name into the launch dir and into ``~/.amplifier/skills``.

    The launch dir is searched before the user dir, so the launch-dir copy must win and
    the home copy must be reported under ``shadowed``. The two files carry different
    descriptions (SHADOW-WS-COPY / SHADOW-HOME-COPY) so a failure message names which
    file won.

    Yields the launch directory the case runs from.
    """
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "shadow-probe-ws" / "SKILL.md"),
        f"{WS_SKILLS}/.amplifier/skills/{SHADOW_SKILL}/SKILL.md",
    )
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "shadow-probe-home" / "SKILL.md"),
        f"{HOME_SKILLS}/{SHADOW_SKILL}/SKILL.md",
    )
    try:
        yield WS_SKILLS
    finally:
        _rm(dtu_id, f"{HOME_SKILLS}/{SHADOW_SKILL}")


@pytest.fixture
def shadowed_builtin_skill_ws(dtu_id: str) -> str:
    """Seed a ``code-review`` override into the LAUNCH dir, colliding with the built-in.

    No teardown: this lives under ``/root/e2e/ws-shadow-skills`` and is therefore only
    visible to a command launched from that directory. Unlike the home-dir seeds it
    cannot leak into any other case's listing.
    """
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "code-review-ws" / "SKILL.md"),
        f"{WS_SKILLS}/.amplifier/skills/{BUILTIN_SKILL}/SKILL.md",
    )
    return WS_SKILLS


@pytest.fixture
def shadowed_builtin_skill_home(dtu_id: str) -> Generator[str, None, None]:
    """Seed a ``code-review`` override into ``~/.amplifier/skills``.

    Deliberately NOT the launch dir: the HTTP server's working directory is not something
    a test controls, so a launch-dir collision would be invisible over HTTP. A home-dir
    collision is visible from any cwd.

    Yields the seeded SKILL.md path. Removed on teardown -- see the module docstring.
    """
    dest = f"{HOME_SKILLS}/{BUILTIN_SKILL}/SKILL.md"
    dtu.push_file(dtu_id, str(FIXTURES / "code-review-home" / "SKILL.md"), dest)
    try:
        yield dest
    finally:
        _rm(dtu_id, f"{HOME_SKILLS}/{BUILTIN_SKILL}")


# --------------------------------------------------------------------------- #
# Mode collisions
# --------------------------------------------------------------------------- #


@pytest.fixture
def shadowed_mode(dtu_id: str) -> Generator[str, None, None]:
    """Seed the SAME mode name into the launch dir and into ``~/.amplifier/modes``.

    Mode roots are searched launch-dir first, then user, then the vendored built-ins --
    the same order the ACTIVATION path uses -- so the launch-dir copy must win.

    Yields the launch directory the case runs from.
    """
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "shadow-mode-ws.md"),
        f"{WS_MODES}/.amplifier/modes/{SHADOW_MODE}.md",
    )
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "shadow-mode-home.md"),
        f"{HOME_MODES}/{SHADOW_MODE}.md",
    )
    try:
        yield WS_MODES
    finally:
        _rm(dtu_id, f"{HOME_MODES}/{SHADOW_MODE}.md")


@pytest.fixture
def shadowed_plan_mode(dtu_id: str) -> str:
    """Seed a ``plan`` override carrying the sentinel into the LAUNCH dir.

    No teardown, for the same reason as ``shadowed_builtin_skill_ws``: it is scoped to
    ``/root/e2e/ws-shadow-plan`` and invisible to anything launched elsewhere.
    """
    dtu.push_file(
        dtu_id,
        str(FIXTURES / "plan-ws.md"),
        f"{WS_PLAN}/.amplifier/modes/{BUILTIN_MODE}.md",
    )
    return WS_PLAN


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


@pytest.fixture
def shadow_server(dtu_id: str, shadowed_builtin_skill_home: str) -> Generator[dict[str, str], None, None]:
    """Start a DEDICATED in-DTU HTTP server, AFTER the colliding skill is seeded.

    Depending on ``shadowed_builtin_skill_home`` is the whole point: fixture setup runs
    dependencies first, so the seed provably exists on disk before this server boots and
    freezes its skill listing. Restarting the shared session server would have worked too,
    but it is session-scoped and shared with every other suite, so a restart mid-session
    is a side effect on tests that never asked for one.

    Yields ``{"base_url", "token"}`` in the shape ``harness.run_http_case`` expects, so
    ``framework/`` needs no changes.
    """
    start = (
        "mkdir -p /root/e2e && "
        f"nohup amplifier-agent serve chat-completions "
        f"--bind 0.0.0.0 --port {SHADOW_PORT} --api-key {SHADOW_TOKEN} "
        f">{SHADOW_LOG} 2>&1 &"
    )
    dtu.exec_json(dtu_id, ["bash", "-lc", start])

    probe = (
        f"curl -s -o /dev/null -w '%{{http_code}}' "
        f"-H 'Authorization: Bearer {SHADOW_TOKEN}' {SHADOW_BASE_URL}/v1/models"
    )
    deadline = time.monotonic() + 90
    ready = False
    while time.monotonic() < deadline:
        result = dtu.exec_json(dtu_id, ["bash", "-lc", probe])
        if result.get("exit_code") == 0 and result.get("stdout", "").strip() == "200":
            ready = True
            break
        time.sleep(3)

    if not ready:
        dtu.exec_json(dtu_id, ["bash", "-lc", _SHADOW_PKILL])
        log = dtu.exec_json(dtu_id, ["bash", "-lc", f"cat {SHADOW_LOG} 2>/dev/null || true"])
        pytest.fail(
            f"the shadowing suite's own server did not become ready on port {SHADOW_PORT}\n"
            f"{SHADOW_LOG}:\n{log.get('stdout', '')}"
        )

    try:
        yield {"base_url": SHADOW_BASE_URL, "token": SHADOW_TOKEN}
    finally:
        dtu.exec_json(dtu_id, ["bash", "-lc", _SHADOW_PKILL])
