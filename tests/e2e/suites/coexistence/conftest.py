"""Fixtures for the coexistence suite: install amplifier-app-cli INSIDE the DTU.

Every other e2e suite runs in a container where amplifier-app-cli was never
installed, so ``~/.amplifier`` holds nothing but whatever a fixture seeded there.
That is the easy case. The case a real user is in is both applications installed
side by side, with app-cli's LIVE module clones sitting under ``~/.amplifier``, and
that is the case this suite constructs.

The install is LAZY and lives here rather than in the DTU profile's ``setup_cmds``
on purpose: it is a full dependency-tree download plus a bundle prepare, and no
other suite should pay for it. Nothing happens until a test in this suite asks.

Two fixtures, in dependency order:

* ``app_cli`` installs amplifier-app-cli and gets it to actually populate
  ``~/.amplifier`` with its real clones. Populating matters more than installing:
  an installed-but-never-run app-cli leaves an almost empty tree, and every
  assertion in this suite would then be comparing nothing to nothing and passing
  for the wrong reason. So the fixture verifies the clones landed and fails loud
  if they did not.
* ``agent_workout`` snapshots ``~/.amplifier``, exercises amplifier-agent hard,
  and snapshots it again. It is session-scoped and shared, so the expensive
  workload runs ONCE and the tests that assert different things about it (the tree
  is unchanged; app-cli still works afterwards) cannot disagree about which run
  they are talking about, and do not depend on being declared in a given order.
"""

from __future__ import annotations

import shlex
from typing import Any

import pytest
from framework import dtu
from framework.progress import log, sub

# --------------------------------------------------------------------------- #
# In-DTU paths
# --------------------------------------------------------------------------- #

# amplifier-app-cli's tree. amplifier-agent must never write here.
APP_CLI_HOME = "/root/.amplifier"
APP_CLI_CACHE = f"{APP_CLI_HOME}/cache"

# amplifier-agent's tree, and the foundation subtree it binds AMPLIFIER_HOME to.
AGENT_HOME = "/root/.amplifier-agent"
AGENT_FOUNDATION_HOME = f"{AGENT_HOME}/foundation"
AGENT_MODULE_CACHE = f"{AGENT_FOUNDATION_HOME}/cache"

# Host-config seeded into every DTU by provisioning (anthropic provider, approval "yes").
CONFIG = "/root/e2e/host-config.json"

# --------------------------------------------------------------------------- #
# app-cli install
# --------------------------------------------------------------------------- #

# Verified against a host install: this is how amplifier-app-cli ships. The console
# script it installs is ``amplifier`` (amplifier-agent's is ``amplifier-agent``), so
# the two never collide on PATH.
_INSTALL_CMD = "uv tool install git+https://github.com/microsoft/amplifier-app-cli@main"

# The cheapest deterministic command that makes app-cli PREPARE its bundle, which is
# what clones its modules into ~/.amplifier/cache. It mounts the active bundle and
# prints the tool list; no model call is involved, so it does not depend on a
# provider answering and cannot vary run to run the way a real turn would.
#
# The alternatives were all worse. ``amplifier module list`` prints "No installed
# modules found" without touching the network, and ``amplifier module update``
# prints "No module cache found" and returns -- both are pure reads of a cache that
# does not exist yet. app-cli has no ``doctor``. A real ``amplifier run`` would work
# (ANTHROPIC_API_KEY is available in the DTU) but costs a model call for a result we
# do not read.
_PRIME_CMD = "amplifier tool list"

# A prepared app-cli bundle clones its whole module set. Ten is far below the ~28
# observed and far above anything an empty or half-failed prepare would leave, so it
# separates "populated" from "not populated" without pinning an exact module count
# that upstream is free to change.
_MIN_CLONES = 10


def _clone_count(dtu_id: str, cache_root: str) -> int:
    """Count top-level ``amplifier-*`` clone directories under a foundation cache root."""
    script = f"ls -1d {shlex.quote(cache_root)}/amplifier-* 2>/dev/null | wc -l"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", script])
    return int((result.get("stdout") or "0").strip() or 0)


@pytest.fixture(scope="session")
def app_cli(dtu_id: str) -> str:
    """Install amplifier-app-cli in the DTU and make it populate ``~/.amplifier``.

    Returns the in-DTU path of app-cli's home. Fails the suite (rather than skipping)
    when the tree did not gain module clones: a silently empty ``~/.amplifier`` would
    make every assertion in this suite vacuous, and a vacuous green is worse than a
    red.

    Note on timeouts: ``dtu.exec_json`` blocks until the command finishes with no
    timeout of its own, so a slow install cannot be cut short. The risk is an
    operator reading a long silence as a hang, which is what the progress logging
    below is for.
    """
    installed = dtu.exec_json(dtu_id, ["bash", "-lc", "command -v amplifier || true"])
    if (installed.get("stdout") or "").strip():
        sub("app-cli already installed in the DTU; skipping install")
    else:
        log("app-cli: installing amplifier-app-cli inside the DTU (downloads a dependency tree; slow)...")
        result = dtu.exec_json(dtu_id, ["bash", "-lc", _INSTALL_CMD])
        assert result.get("exit_code") == 0, (
            "installing amplifier-app-cli in the DTU failed\n"
            f"command: {_INSTALL_CMD}\n"
            f"stdout:\n{result.get('stdout', '')}\n"
            f"stderr:\n{result.get('stderr', '')}"
        )
        log("app-cli: installed")

    before = _clone_count(dtu_id, APP_CLI_CACHE)
    log(f"app-cli: priming its bundle cache with `{_PRIME_CMD}` (clones its modules; slow on first run)...")
    primed = dtu.exec_json(dtu_id, ["bash", "-lc", _PRIME_CMD])
    assert primed.get("exit_code") == 0, (
        f"`{_PRIME_CMD}` failed inside the DTU, so app-cli never populated {APP_CLI_HOME}\n"
        f"stdout:\n{primed.get('stdout', '')}\n"
        f"stderr:\n{primed.get('stderr', '')}"
    )

    after = _clone_count(dtu_id, APP_CLI_CACHE)
    sub(f"app-cli: {APP_CLI_CACHE} holds {after} clone directories (was {before})")
    if after < _MIN_CLONES:
        pytest.fail(
            f"amplifier-app-cli is installed but did not populate {APP_CLI_CACHE}: "
            f"found {after} `amplifier-*` clone directories, expected at least {_MIN_CLONES}.\n"
            f"Every assertion in this suite compares that tree before and after amplifier-agent runs, "
            f"so an empty tree would make all of them pass without proving anything.\n"
            f"`{_PRIME_CMD}` output was:\n{primed.get('stdout', '')}\n{primed.get('stderr', '')}"
        )

    return APP_CLI_HOME


# --------------------------------------------------------------------------- #
# The amplifier-agent workload
# --------------------------------------------------------------------------- #

# Every amplifier-agent surface that could plausibly reach a foundation path, run
# against a fully populated app-cli tree. Ordering is deliberate: `cache clear`
# drops the prepared-bundle cache so the LAST turn has to re-prepare the whole
# bundle from scratch, which is the write-heaviest path amplifier-agent has and the
# one that would land in ~/.amplifier if the AMPLIFIER_HOME bind ever stopped
# running. Running it under observation is the point.
#
# `update --check` rather than a bare `update`: the installing form runs
# `uv tool install --reinstall --force`, which wipes amplifier-agent's lazily
# installed provider module and breaks `serve` for every other suite sharing this
# warm DTU (see docs/E2E_TESTING.md on why `run` rebuilds rather than refreshes).
# `--check` still exercises release resolution and the same path resolution.
_WORKOUT: tuple[tuple[str, str], ...] = (
    ("run", f"amplifier-agent run -y --config {CONFIG} 'reply with a short greeting'"),
    ("doctor", "amplifier-agent doctor"),
    ("config-show", "amplifier-agent config show"),
    ("update-check", "amplifier-agent update --check"),
    ("skills-list", "amplifier-agent skills list"),
    ("modes-list", "amplifier-agent modes list"),
    ("cache-clear", "amplifier-agent cache clear"),
    (
        "run-skills",
        f"amplifier-agent run -y --config {CONFIG} "
        "'Use the load_skill tool to list the available skills, then reply DONE.'",
    ),
)

# Commands whose failure means the workload did not actually happen, so a later
# "nothing changed" assertion would be trivially true. `update --check` is excluded
# because it depends on the GitHub releases API being reachable from inside the
# container, which is a fact about the network rather than about amplifier-agent.
_MUST_SUCCEED = frozenset({"run", "doctor", "config-show", "cache-clear", "run-skills"})


@pytest.fixture(scope="session")
def agent_workout(dtu_id: str, app_cli: str) -> dict[str, Any]:
    """Snapshot ``~/.amplifier``, exercise amplifier-agent hard, snapshot it again.

    Returns ``{"before": TreeState, "after": TreeState, "results": {name: result}}``.

    The snapshots are taken with NO exclusions. Which paths to ignore is a policy
    belonging to the assertion, not to the recording, so the test applies its own
    exclusions to a full picture rather than trusting this fixture to have recorded
    the right subset.
    """
    from suites.coexistence import tree

    log(f"coexistence: recording {APP_CLI_HOME} before exercising amplifier-agent...")
    before = tree.snapshot(dtu_id, APP_CLI_HOME)
    sub(f"{len(before.files)} files, {len(before.dirs)} directories")

    results: dict[str, dict[str, Any]] = {}
    for name, command in _WORKOUT:
        log(f"coexistence: amplifier-agent {name}...")
        result = dtu.exec_json(dtu_id, ["bash", "-lc", command])
        results[name] = result
        sub(f"exit {result.get('exit_code')}")
        if name in _MUST_SUCCEED:
            assert result.get("exit_code") == 0, (
                f"the workload step `{name}` failed, so this suite would be asserting that "
                f"amplifier-agent left {APP_CLI_HOME} alone while it was not actually doing anything\n"
                f"command: {command}\n"
                f"stdout:\n{result.get('stdout', '')}\n"
                f"stderr:\n{result.get('stderr', '')}"
            )

    log(f"coexistence: recording {APP_CLI_HOME} again...")
    after = tree.snapshot(dtu_id, APP_CLI_HOME)
    sub(f"{len(after.files)} files, {len(after.dirs)} directories")

    return {"before": before, "after": after, "results": results}
