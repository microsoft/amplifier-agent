"""Unit tests for shadow reporting in ``amplifier_agent_lib.resources``.

Discovery is first-match-wins across an ordered list of roots. The loser used to
vanish silently, so a user whose override was ignored had no way to find out.
Every entry now carries the winning ``source`` plus a ``shadowed`` list naming
every same-named file that lost.

Two properties are load-bearing and are pinned separately here:

* **A real collision is reported.** ``shadowed`` names the file that lost, so the
  CLI table and the HTTP payload can surface it.
* **A non-collision is NOT reported.** ``shadowed == []`` must hold whenever
  nothing was actually discarded -- including the case where the same directory
  is reached by two different roots. When the process CWD is the home directory
  (exactly how the HTTP server is launched), ``<cwd>/.amplifier/skills`` and
  ``~/.amplifier/skills`` are one directory, and without the resolved-path
  collapse in ``_dedupe_roots`` every skill would report shadowing itself. A
  false conflict is worse than no report: it trains users to ignore the marker.

Every test runs against a synthetic filesystem under ``tmp_path`` with ``$HOME``,
the CWD, and the vendored built-in dirs all redirected there, so the real home
directory and the real shipped skills/modes never participate.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_agent_lib import resources

# ---------------------------------------------------------------------------
# Fixtures and fixture helpers
# ---------------------------------------------------------------------------


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str,
    slash_command: bool = True,
) -> Path:
    """Create ``<root>/<name>/SKILL.md`` and return its path.

    ``slash_command`` controls ``disable-model-invocation``, which is the exact
    predicate ``list_skills`` filters on -- a skill without it is model-invocable
    and is not listed.
    """
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    front = [f"name: {name}", f"description: {description}"]
    if slash_command:
        front.append("disable-model-invocation: true")
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\n" + "\n".join(front) + "\n---\n\nBody.\n", encoding="utf-8")
    return skill_file


def _write_mode(root: Path, name: str, *, description: str) -> Path:
    """Create a parseable ``<root>/<name>.md`` mode file and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    mode_file = root / f"{name}.md"
    mode_file.write_text(
        f"---\nmode:\n  name: {name}\n  description: {description}\n  tools:\n    safe: []\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return mode_file


def _write_broken_mode(root: Path, name: str) -> Path:
    """Create an UNPARSEABLE ``<root>/<name>.md`` (no YAML frontmatter)."""
    root.mkdir(parents=True, exist_ok=True)
    mode_file = root / f"{name}.md"
    mode_file.write_text("Just markdown. No frontmatter, so parse_mode_file returns None.\n", encoding="utf-8")
    return mode_file


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Redirect every discovery root into ``tmp_path``.

    Three redirections are needed because the roots come from three different
    mechanisms: the vendored built-in dirs are module constants, the workspace
    dirs are relative to the process CWD, and the user dirs come from ``$HOME``
    via ``expanduser()``. ``AMPLIFIER_SKILLS_DIR`` is cleared because
    ``get_default_skills_dirs`` prepends it when set, which would silently add a
    fourth root from the developer's own environment.
    """
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    builtin_skills = tmp_path / "builtin" / "skills"
    builtin_modes = tmp_path / "builtin" / "modes"
    for path in (home, cwd, builtin_skills, builtin_modes):
        path.mkdir(parents=True)

    monkeypatch.delenv("AMPLIFIER_SKILLS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(resources, "_BUILTIN_SKILLS_DIR", builtin_skills)
    monkeypatch.setattr(resources, "_BUILTIN_MODES_DIR", builtin_modes)

    return SimpleNamespace(
        home=home,
        cwd=cwd,
        builtin_skills=builtin_skills,
        builtin_modes=builtin_modes,
        cwd_skills=cwd / ".amplifier" / "skills",
        user_skills=home / ".amplifier" / "skills",
        cwd_modes=cwd / ".amplifier" / "modes",
        user_modes=home / ".amplifier" / "modes",
    )


def _by_name(entries: list[dict], name: str) -> dict:
    """Return the single entry with ``name``, asserting there is exactly one."""
    matches = [e for e in entries if e["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} entry, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# list_skills -- collisions across distinct roots
# ---------------------------------------------------------------------------


def test_skill_collision_reports_one_entry_with_the_loser_shadowed(roots: SimpleNamespace) -> None:
    """A same-named skill in two roots collapses to one entry naming both files.

    Root order puts the built-in dir first, so the built-in is what actually
    runs and the workspace copy is the one discarded. The pre-fix behavior was
    identical up to ``source``; the entire point of this test is the
    ``shadowed`` list, which is what makes the discarded override visible.
    """
    winner = _write_skill(roots.builtin_skills, "dup", description="builtin copy")
    loser = _write_skill(roots.cwd_skills, "dup", description="workspace copy")

    skills = resources.list_skills()

    entry = _by_name(skills, "dup")
    assert entry["source"] == str(winner)
    assert entry["description"] == "builtin copy"
    assert entry["shadowed"] == [{"source": str(loser)}]


def test_skill_without_collision_reports_empty_shadowed(roots: SimpleNamespace) -> None:
    """``shadowed`` is ALWAYS present, so consumers never branch on its absence.

    An empty list rather than a missing key is what lets the CLI renderer and
    the HTTP payload treat "no conflict" as data instead of as a special case.
    """
    _write_skill(roots.builtin_skills, "alpha", description="builtin only")
    _write_skill(roots.cwd_skills, "beta", description="workspace only")

    skills = resources.list_skills()

    assert [s["name"] for s in skills] == ["alpha", "beta"]
    assert all(s["shadowed"] == [] for s in skills)


def test_same_directory_reached_by_two_roots_does_not_self_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``$HOME``-as-CWD regression guard: one directory, two roots, no conflict.

    ``get_default_skills_dirs`` returns the RELATIVE ``.amplifier/skills`` and the
    absolute ``~/.amplifier/skills``. When the process runs from the home
    directory -- which is exactly how the HTTP server is launched -- those name
    the same directory. Discovery walks it once per root, so without collapsing
    roots by RESOLVED path first, the second pass finds every skill already
    claimed and reports it as shadowing itself: a conflict warning on a machine
    with no conflict, on every single skill.
    """
    home = tmp_path / "home"
    home.mkdir()
    builtin = tmp_path / "builtin" / "skills"
    builtin.mkdir(parents=True)

    monkeypatch.delenv("AMPLIFIER_SKILLS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)  # CWD == HOME: the two roots now resolve identically
    monkeypatch.setattr(resources, "_BUILTIN_SKILLS_DIR", builtin)

    skill_file = _write_skill(home / ".amplifier" / "skills", "solo", description="the only copy")

    skills = resources.list_skills()

    entry = _by_name(skills, "solo")
    assert entry["source"] == str(skill_file)
    assert entry["shadowed"] == [], "a directory reached twice is one root, not a collision"


def test_disable_model_invocation_is_evaluated_on_the_winner(roots: SimpleNamespace) -> None:
    """The filter applies to the skill that would RUN, not to any loser.

    The winner here is model-invocable, so ``dup`` is not a slash command and
    must not be listed -- even though the shadowed workspace copy declares
    ``disable-model-invocation``. Evaluating the predicate on the loser would
    advertise a slash command that cannot be invoked, because invoking it would
    reach the built-in.
    """
    _write_skill(roots.builtin_skills, "dup", description="builtin, model-invocable", slash_command=False)
    _write_skill(roots.cwd_skills, "dup", description="workspace, slash command", slash_command=True)
    _write_skill(roots.builtin_skills, "listed", description="a real slash command")

    skills = resources.list_skills()

    assert [s["name"] for s in skills] == ["listed"]


# ---------------------------------------------------------------------------
# list_modes -- collisions and the unparseable-file case
# ---------------------------------------------------------------------------


def test_mode_collision_lets_the_workspace_copy_win(roots: SimpleNamespace) -> None:
    """Mode root order is the OPPOSITE of skills': workspace first, builtin last.

    That order mirrors the activation path (``hooks-mode`` mounts the project
    and user search paths before the bundle dirs), so the mode reported here is
    the mode that actually runs. If this ever flipped to match the skills
    ordering, ``modes list`` would name a file that a turn would never load.
    """
    winner = _write_mode(roots.cwd_modes, "plan", description="workspace override")
    loser = _write_mode(roots.builtin_modes, "plan", description="builtin default")

    modes = resources.list_modes()

    entry = _by_name(modes, "plan")
    assert entry["source"] == str(winner)
    assert entry["description"] == "workspace override"
    assert entry["shadowed"] == [{"source": str(loser)}]


def test_unparseable_mode_file_claims_nothing_and_shadows_nothing(roots: SimpleNamespace) -> None:
    """A broken file in a higher-priority root neither wins nor counts as a conflict.

    Two distinct failures are guarded at once. If the broken file CLAIMED the
    name, the valid lower-priority mode would disappear from the listing while
    ``hooks-mode`` (which skips unparseable files the same way) still activated
    it -- the listing would contradict reality. If it were instead recorded in
    ``shadowed``, the user would be told their working mode was overridden by a
    file that does nothing.
    """
    broken = _write_broken_mode(roots.cwd_modes, "plan")
    valid = _write_mode(roots.builtin_modes, "plan", description="builtin default")

    modes = resources.list_modes()

    entry = _by_name(modes, "plan")
    assert entry["source"] == str(valid)
    assert entry["description"] == "builtin default"
    assert entry["shadowed"] == []
    assert str(broken) not in str(modes), "an unparseable file must not appear anywhere in the payload"


def test_mode_without_collision_reports_empty_shadowed(roots: SimpleNamespace) -> None:
    """Counterpart to the skills case: ``shadowed`` is always present for modes too."""
    _write_mode(roots.cwd_modes, "workspace-only", description="workspace")
    _write_mode(roots.builtin_modes, "builtin-only", description="builtin")

    modes = resources.list_modes()

    assert [m["name"] for m in modes] == ["builtin-only", "workspace-only"]
    assert all(m["shadowed"] == [] for m in modes)
