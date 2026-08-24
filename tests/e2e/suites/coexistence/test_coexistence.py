"""DTU-backed tests for amplifier-agent coexisting with amplifier-app-cli.

``docs/spec/foundation-cache-ownership.md`` makes one guarantee about a machine that
has both applications on it: amplifier-agent operates entirely from
``~/.amplifier-agent`` and leaves ``~/.amplifier`` -- amplifier-app-cli's tree, and on
a real machine its LIVE module clones -- strictly alone. Not "mostly alone", and not
"alone except for the parts we thought were shared". The spec is explicit that
existing clones there are left in place, that no cleanup affordance exists, and that
the two populations of user are indistinguishable from inside amplifier-agent.

That guarantee is only interesting when app-cli is actually installed and its cache
actually populated, which no other suite arranges. ``conftest.py`` arranges it, and
these tests assert against it.

The regression being guarded is silent by construction. If the ``AMPLIFIER_HOME``
bind in ``amplifier_agent_lib/__init__.py`` ever stops running, foundation falls back
to ``~/.amplifier``, every module clone returns to app-cli's tree, and amplifier-agent
keeps working perfectly. Nothing reports a problem. The only observable is the
filesystem, so the filesystem is what these tests read.

These are CLI-only, so they request ``dtu_id`` and the suite's own fixtures and never
the shared ``server`` fixture; starting an HTTP server would add a process writing to
paths under test for no benefit.
"""

from __future__ import annotations

from typing import Any

import pytest
from framework import dtu

from suites.coexistence import tree
from suites.coexistence.conftest import AGENT_FOUNDATION_HOME, AGENT_MODULE_CACHE, APP_CLI_CACHE, APP_CLI_HOME

pytestmark = pytest.mark.dtu

# --------------------------------------------------------------------------- #
# What is excluded from the "nothing changed" comparison, and why
# --------------------------------------------------------------------------- #

# ``~/.amplifier/cache/skills`` used to be excluded from the comparison below. Upstream
# ``tool-skills`` hardcoded it as its remote-skill clone root, bypassing AMPLIFIER_HOME,
# so it was written no matter what amplifier-agent did and excluding it was the only way
# the primary test could speak about a defect it owned. microsoft/amplifier-bundle-skills#61
# has since merged, and upstream main computes that root from AMPLIFIER_HOME
# (``default_skills_cache_dir()``), so amplifier-agent no longer writes there at all. The
# subtree is now part of the primary comparison, which makes that test strictly stronger.
# ``test_remote_skill_clones_stay_out_of_app_cli_tree`` still asserts against this subtree
# on its own, with narrower claims.
SKILLS_CACHE_SUBTREE = "cache/skills"

# Other e2e suites deliberately seed files into ``/root/.amplifier/skills`` and
# ``/root/.amplifier/modes`` -- see ``suites/shadowing/conftest.py`` (HOME_SKILLS /
# HOME_MODES) and ``suites/skills/conftest.py`` (the memory probe). In the DTU
# ``/root`` is both HOME and the default launch directory, which is precisely why
# those suites use that location. Their fixtures create and remove files there on
# their own schedule, so those subtrees are noise from this suite's point of view and
# are not amplifier-agent writing to app-cli's cache.
SUITE_SEEDED_EXCLUSIONS = ("skills", "modes")

EXCLUDED_PREFIXES = SUITE_SEEDED_EXCLUSIONS

# Where the fixed tool-skills puts its remote clones instead, observed in a real DTU.
AGENT_SKILLS_CACHE = f"{AGENT_MODULE_CACHE}/skills"

# The same path inside app-cli's tree: where a pre-fix tool-skills put every clone, and
# where app-cli's own clones legitimately live today.
APP_CLI_SKILLS_CACHE = f"{APP_CLI_CACHE}/skills"

# microsoft/amplifier-bundle-skills#61 replaced a module-level constant with a function
# that consults AMPLIFIER_HOME. Main carries that function now, and its presence is what
# tells a current tool-skills from a pre-fix one, from outside the process.
_FIXED_SYMBOL = "def default_skills_cache_dir"
_TOOL_SKILLS_SOURCES = (
    f"{AGENT_MODULE_CACHE}/amplifier-bundle-skills-*/modules/tool-skills/amplifier_module_tool_skills/sources.py"
)


def _exec(dtu_id: str, script: str) -> dict[str, Any]:
    """Run a shell snippet inside the DTU."""
    return dtu.exec_json(dtu_id, ["bash", "-lc", script])


# --------------------------------------------------------------------------- #
# 1. The primary test
# --------------------------------------------------------------------------- #


def test_agent_does_not_touch_app_cli_tree(agent_workout: dict[str, Any]) -> None:
    """``~/.amplifier`` is identical before and after amplifier-agent is exercised hard.

    The workload behind this (see ``conftest.py``) is a real turn, ``doctor``,
    ``config show``, ``update --check``, ``skills list``, ``modes list``,
    ``cache clear``, and then a skills-touching turn that has to re-prepare the entire
    bundle because the cache was just cleared. If amplifier-agent reaches into
    app-cli's tree anywhere, that sequence is where it happens.

    The comparison is path + size + mtime for files and symlinks, plus the directory
    set, so a created file, a deleted file, a rewritten file and a bare ``mkdir`` are
    all caught. See ``tree.py`` for why it is not a content hash.
    """
    before = agent_workout["before"]
    after = agent_workout["after"]

    filtered_before = _refilter(before)
    filtered_after = _refilter(after)

    report = tree.diff(filtered_before, filtered_after)
    assert not report, (
        f"{report}\n\n"
        f"amplifier-agent must operate entirely from {AGENT_FOUNDATION_HOME}; "
        f"{APP_CLI_HOME} belongs to amplifier-app-cli and on a real machine holds its live "
        f"module clones (docs/spec/foundation-cache-ownership.md).\n"
        f"The usual cause is the AMPLIFIER_HOME bind in amplifier_agent_lib/__init__.py no longer "
        f"running before amplifier_foundation is imported, which makes foundation fall back to "
        f"~/.amplifier silently.\n"
        f"Excluded from this comparison: {', '.join(EXCLUDED_PREFIXES)}."
    )


def _refilter(state: tree.TreeState) -> tree.TreeState:
    """Drop the excluded prefixes from an already-recorded snapshot.

    The fixture records the tree whole so the exclusion policy lives with the
    assertion that needs it, next to the comment explaining each entry.
    """
    return tree.TreeState(
        root=state.root,
        files={path: value for path, value in state.files.items() if not _excluded(path)},
        dirs=frozenset(path for path in state.dirs if not _excluded(path)),
    )


def _excluded(rel: str) -> bool:
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in EXCLUDED_PREFIXES)


# --------------------------------------------------------------------------- #
# 2. app-cli survives
# --------------------------------------------------------------------------- #


def test_app_cli_still_works_after_agent_runs(dtu_id: str, agent_workout: dict[str, Any]) -> None:
    """amplifier-app-cli still runs cleanly after all that amplifier-agent activity.

    Test 1 asserts the tree did not change; this asserts the thing the user actually
    cares about, which is not a filesystem property at all. The CHANGELOG's stated
    failure mode is a user breaking an application they depend on without meaning to,
    and a broken app-cli is how they would find out. A comparison of file listings can
    in principle miss a way to break it (a lock file rewritten to identical size and
    mtime, a permissions change), so this runs the program.
    """
    result = _exec(dtu_id, "amplifier tool list")
    exit_code = result.get("exit_code")
    stdout = result.get("stdout", "")

    assert exit_code == 0, (
        f"`amplifier tool list` (amplifier-app-cli) exited {exit_code} after amplifier-agent ran.\n"
        f"amplifier-agent broke a different application installed on the same machine.\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{result.get('stderr', '')}"
    )
    assert "tools" in stdout, (
        "amplifier-app-cli exited 0 but printed no tool listing after amplifier-agent ran, "
        f"which suggests its bundle no longer prepares correctly.\nstdout:\n{stdout}"
    )


# --------------------------------------------------------------------------- #
# 3. Remote skill clones
# --------------------------------------------------------------------------- #


def test_remote_skill_clones_stay_out_of_app_cli_tree(dtu_id: str, agent_workout: dict[str, Any]) -> None:
    """Remote skill clones land under the agent's foundation home, not app-cli's cache.

    Test 1 now covers this subtree too, but only as "nothing under ``~/.amplifier``
    changed". This says the positive half: the clones exist, and they exist under
    amplifier-agent's own cache root.

    The capability probe below stays even though the fix is upstream. A stock DTU
    installs a tool-skills that carries it, but anyone re-provisioning with ``--repo
    amplifier-bundle-skills`` pointed at an older checkout lands on the pre-fix module,
    and a clear skip beats a failure that looks like an amplifier-agent regression and
    is not one. That is also why this is not a strict xfail: it has to give the right
    answer in both containers without anyone editing it.

    Note what is NOT asserted: that ``~/.amplifier/cache/skills`` is absent. That is
    app-cli's own skills cache root, since ``~/.amplifier`` is app-cli's foundation home,
    and app-cli populates it during its own normal operation. It is entitled to -- it is
    its tree. The claim under test is narrower and is the claim that actually matters:
    amplifier-agent does not write there. So this compares that exact subtree before and
    after the workload.
    """
    probe = _exec(dtu_id, f"grep -l '{_FIXED_SYMBOL}' {_TOOL_SKILLS_SOURCES} 2>/dev/null || true")
    if not (probe.get("stdout") or "").strip():
        pytest.skip(
            "the tool-skills module in this container predates microsoft/amplifier-bundle-skills#61 "
            f"and hardcodes ~/.amplifier/cache/skills (no `{_FIXED_SYMBOL}` in sources.py). Upstream "
            "main carries the fix, so this is a DTU provisioned from an older skills checkout, most "
            "likely via `--repo amplifier-bundle-skills`."
        )

    landed = _exec(dtu_id, f"test -d {AGENT_SKILLS_CACHE} && echo yes || echo no")
    assert (landed.get("stdout") or "").strip() == "yes", (
        f"the fixed tool-skills is installed but {AGENT_SKILLS_CACHE} does not exist, so remote "
        f"skill clones are not landing under amplifier-agent's foundation home. Either no remote "
        f"skill source resolved during the workload, or the cache root is being computed from "
        f"something other than AMPLIFIER_HOME."
    )

    before = _only(agent_workout["before"], SKILLS_CACHE_SUBTREE)
    after = _only(agent_workout["after"], SKILLS_CACHE_SUBTREE)
    report = tree.diff(before, after)
    assert not report, (
        f"{report}\n\n"
        f"{APP_CLI_SKILLS_CACHE} changed while amplifier-agent ran. Remote skill clones are being "
        f"written into amplifier-app-cli's tree; the tool-skills in this container computes that "
        f"root from AMPLIFIER_HOME (microsoft/amplifier-bundle-skills#61, merged), so they belong "
        f"under {AGENT_SKILLS_CACHE}."
    )


def _only(state: tree.TreeState, prefix: str) -> tree.TreeState:
    """Narrow a snapshot to one subtree, so a diff speaks about that subtree alone."""

    def keep(rel: str) -> bool:
        return rel == prefix or rel.startswith(prefix + "/")

    return tree.TreeState(
        root=f"{state.root}/{prefix}",
        files={path: value for path, value in state.files.items() if keep(path)},
        dirs=frozenset(path for path in state.dirs if keep(path)),
    )


# --------------------------------------------------------------------------- #
# 4. The guard is not vacuous
# --------------------------------------------------------------------------- #


def test_isolation_guard_actually_fires(dtu_id: str) -> None:
    """``doctor``'s foundation-isolation check fails when isolation is actually broken.

    Test 1 can only ever say "nothing bad happened". This says the alarm works, which
    is the other half: a guard that cannot fail is decoration, and this one is the
    standing runtime check for exactly the silent regression this suite is about
    (``admin/doctor.py:146-150``).

    The obvious lever does NOT work, and knowing why matters so nobody "fixes" this
    test by reaching for it. Unsetting ``AMPLIFIER_HOME`` proves nothing:
    ``amplifier_agent_lib/__init__.py:44`` calls ``foundation_home.bind()`` at package
    import and unconditionally overwrites the variable, so
    ``env -u AMPLIFIER_HOME amplifier-agent doctor`` re-binds it and prints ``[ OK ]``.
    That is the bind working as designed. The unset-at-import case is unreachable from
    outside the process and can only be exercised by breaking the import order itself,
    which is not something a subprocess can arrange.

    ``AMPLIFIER_AGENT_FOUNDATION_HOME`` is the lever that IS reachable: it is a
    supported override that ``bind()`` honours, so pointing it inside ``~/.amplifier``
    produces exactly the state the guard exists to catch.
    """
    broken = _exec(dtu_id, "AMPLIFIER_AGENT_FOUNDATION_HOME=$HOME/.amplifier/foundation amplifier-agent doctor")
    combined = (broken.get("stdout") or "") + (broken.get("stderr") or "")

    assert broken.get("exit_code") != 0, (
        "amplifier-agent doctor exited 0 with AMPLIFIER_AGENT_FOUNDATION_HOME pointed inside "
        "~/.amplifier. The foundation-isolation guard did not fire, so it would not catch the "
        f"real regression either.\noutput:\n{combined}"
    )

    failures = [line for line in combined.splitlines() if line.startswith("[FAIL] foundation isolation:")]
    assert failures, (
        "amplifier-agent doctor failed, but not with a `[FAIL] foundation isolation:` line, so "
        "something OTHER than the isolation guard is what failed and this test is not proving "
        f"what it claims.\noutput:\n{combined}"
    )
    joined = "\n".join(failures)
    assert any(".amplifier" in line for line in failures), (
        "the foundation-isolation failure line does not name ~/.amplifier, so it is not the "
        f"inside-app-cli's-tree condition (admin/doctor.py:146-150).\nlines:\n{joined}"
    )

    healthy = _exec(dtu_id, "amplifier-agent doctor")
    healthy_out = (healthy.get("stdout") or "") + (healthy.get("stderr") or "")
    assert any(line.startswith("[ OK ] foundation isolation:") for line in healthy_out.splitlines()), (
        "without the override, amplifier-agent doctor does not report `[ OK ] foundation isolation:`. "
        "The guard fires on the broken case but does not pass on the healthy one, so it reports "
        f"nothing useful.\noutput:\n{healthy_out}"
    )


# --------------------------------------------------------------------------- #
# 5. Same basename, different storage
# --------------------------------------------------------------------------- #


def test_clone_dirs_are_independent_not_aliased(dtu_id: str, agent_workout: dict[str, Any]) -> None:
    """Where both cache roots hold the same directory name, they are separate storage.

    Foundation keys every clone as ``sha256(git_url@ref)[:16]`` with no
    per-application namespacing (amplifier-foundation ``sources/git.py:396-402``), so
    nothing in the naming scheme stops the two trees from containing a directory with
    the SAME basename for the same repo at the same ref. The spec leans on that fact
    twice: it is why amplifier-agent cannot tell an agent-only user's stale clones
    from an app-cli user's live ones, and therefore why no cleanup affordance exists.

    A shared name is fine. A shared inode is not: a symlink or hardlink between the
    trees would mean amplifier-agent's writes land in app-cli's storage while every
    path-based check in this suite still reads clean. So this compares device and
    inode numbers and rejects links in either direction.

    In practice the colliding set today is small, because amplifier-agent resolves
    ``@main`` to a commit sha before foundation computes the key (``bundle/pinning.py``)
    while app-cli leaves it floating, so the same repository usually hashes to two
    different directory names. Skips rather than passing when there is no collision at
    all, since a comparison of an empty set proves nothing.
    """
    script = (
        f"comm -12 "
        f"<(cd {APP_CLI_CACHE} 2>/dev/null && find . -maxdepth 1 -mindepth 1 -type d -printf '%f\\n' | sort) "
        f"<(cd {AGENT_MODULE_CACHE} 2>/dev/null && find . -maxdepth 1 -mindepth 1 -type d -printf '%f\\n' | sort)"
    )
    result = _exec(dtu_id, script)
    colliding = [name for name in (result.get("stdout") or "").splitlines() if name.strip()]

    if not colliding:
        pytest.skip(
            f"no directory basename appears in both {APP_CLI_CACHE} and {AGENT_MODULE_CACHE} in this "
            f"container, so there is no aliasing to disprove. amplifier-agent pins @main to a commit "
            f"sha before the cache key is computed while app-cli leaves it floating, so the same "
            f"repository normally hashes to two different names."
        )

    for name in colliding:
        app_path = f"{APP_CLI_CACHE}/{name}"
        agent_path = f"{AGENT_MODULE_CACHE}/{name}"

        # %d = device number, %i = inode. Equal pairs mean one storage location.
        ids = _exec(dtu_id, f"stat -c '%d %i' {app_path} {agent_path}")
        assert ids.get("exit_code") == 0, f"could not stat {app_path} / {agent_path}: {ids.get('stderr', '')}"
        lines = (ids.get("stdout") or "").split()
        assert len(lines) == 4, f"unexpected stat output for {name}: {ids.get('stdout', '')!r}"
        app_dev, app_ino, agent_dev, agent_ino = lines

        assert (app_dev, app_ino) != (agent_dev, agent_ino), (
            f"{app_path} and {agent_path} are the SAME directory (device {app_dev}, inode {app_ino}). "
            f"amplifier-agent's clones are not independent storage, so writing to its own cache root "
            f"writes into amplifier-app-cli's tree."
        )

        links = _exec(dtu_id, f"test -L {app_path} && echo app; test -L {agent_path} && echo agent; true")
        assert not (links.get("stdout") or "").strip(), (
            f"a symlink connects {app_path} and {agent_path} "
            f"(symlinked: {(links.get('stdout') or '').split()}). The two cache roots must be "
            f"independent storage, not two names for one directory."
        )
