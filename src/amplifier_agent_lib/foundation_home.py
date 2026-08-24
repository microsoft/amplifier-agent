"""Bind amplifier_foundation's storage root into amplifier-agent's own tree.

Why this module exists
----------------------
Every on-disk artefact amplifier-agent *knows* it owns already lives under
``amplifier_agent_home()`` (``~/.amplifier-agent`` by default, overridable via
``$AMPLIFIER_AGENT_HOME``) -- state, config, credentials, and the
prepared-bundle cache.  One category did not: the git clones of the modules the
bundle declares.

Those clones are created by ``amplifier_foundation``, not by this application.
Foundation resolves every location it owns through
``amplifier_foundation.paths.get_amplifier_home()``::

    env_home = os.environ.get("AMPLIFIER_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".amplifier").resolve()

amplifier-agent never set ``AMPLIFIER_HOME``, so the fallback applied and every
``amplifier-module-*`` clone landed in ``~/.amplifier/cache`` -- a tree owned
and managed by *amplifier-app-cli*, a different application with a different
release cadence and a known-buggy cache design.  Nothing in this repository
referenced ``.amplifier`` to make that happen; the coupling was created by the
*absence* of an argument at the ``load_bundle()`` call in
:mod:`amplifier_agent_lib.bundle.loader`, which is why it survived several
rounds of grep-driven cleanup.

The consequences were not theoretical.  Sharing a clone root with another
application means amplifier-agent cannot safely refresh its own module clones
(they may be in use by app-cli), cannot reason about their freshness, and
inherits any corruption app-cli introduces.

What this module does
---------------------
Sets ``AMPLIFIER_HOME`` to a directory inside amplifier-agent's own tree before
``amplifier_foundation`` is imported, so foundation's own resolver -- unchanged,
unpatched, using its documented public contract -- writes into a root this
application owns outright.

Ordering is load-bearing
------------------------
``amplifier_foundation.session`` evaluates a module-level constant at *import*
time::

    # amplifier_foundation/session/finder.py:36
    DEFAULT_SESSIONS_ROOT: Path = Path.home() / ".amplifier" / "projects"

and ``session/__init__.py`` imports ``finder`` unconditionally.  A binding
applied after that import would be too late for anything that constant feeds.
:func:`bind` is therefore invoked at the top of both the ``amplifier_agent_lib``
and ``amplifier_agent_http`` package ``__init__`` modules -- both of which
execute before any ``amplifier_foundation`` import in this codebase, all of
which are either function-local or in submodules.

Unconditional by design
-----------------------
:func:`bind` overwrites any inherited ``AMPLIFIER_HOME``.  That is deliberate
and is the entire point: a user who has exported ``AMPLIFIER_HOME`` to steer
amplifier-app-cli would otherwise silently re-couple amplifier-agent to app-cli's
cache -- reintroducing exactly the bug this module removes, and doing so only on
the machines of the users most likely to have customised their setup.  Callers
who genuinely need to relocate amplifier-agent's foundation tree have two
supported levers, both honoured here: ``$AMPLIFIER_AGENT_FOUNDATION_HOME``
(this subtree only) and ``$AMPLIFIER_AGENT_HOME`` (the whole application tree).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "FOUNDATION_HOME_ENV",
    "FOUNDATION_SUBDIR",
    "LEGACY_MODULE_CLONE_PREFIX",
    "bind",
    "find_legacy_module_clones",
    "foundation_home",
    "legacy_module_clone_root",
    "module_cache_root",
]

#: Prefix foundation gives each module clone directory
#: (``<repo-name>-<sha256(url@ref)[:16]>``).
LEGACY_MODULE_CLONE_PREFIX = "amplifier-module-"

#: Environment variable foundation itself consults.  Set by :func:`bind`.
_FOUNDATION_ENV = "AMPLIFIER_HOME"

#: Environment variable the context-intelligence hook's *readers* consult.
#: Set by :func:`bind` only when absent -- see :func:`_bind_context_intelligence`.
_CONTEXT_INTELLIGENCE_ENV = "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"

#: Escape hatch for relocating *only* the foundation subtree.
FOUNDATION_HOME_ENV = "AMPLIFIER_AGENT_FOUNDATION_HOME"

#: Subdirectory of ``amplifier_agent_home()`` handed to foundation.
#:
#: Kept as a sibling of ``cache/``, ``state/`` and ``config/`` rather than
#: nested inside ``cache/`` so the ownership boundary is legible on disk:
#: everything below ``foundation/`` is written by foundation's resolver on
#: foundation's schedule, and everything beside it is written by this
#: application.  ``amplifier-agent cache clear`` can then reason about the two
#: independently.
FOUNDATION_SUBDIR = "foundation"


def foundation_home() -> Path:
    """Return the directory amplifier-agent hands to foundation as its home.

    Resolves in order:

    1. ``$AMPLIFIER_AGENT_FOUNDATION_HOME`` -- relocate this subtree alone.
    2. ``amplifier_agent_home() / "foundation"`` -- which itself honours
       ``$AMPLIFIER_AGENT_HOME``.

    Never ``~/.amplifier``: that tree belongs to amplifier-app-cli.
    """
    override = os.environ.get(FOUNDATION_HOME_ENV)
    if override:
        return Path(override).expanduser()

    # Imported inside the function, not at module scope, to keep the import
    # graph acyclic.  ``persistence`` does ``from amplifier_agent_lib import
    # __version__``, and :func:`bind` is called from that package's ``__init__``
    # -- a module-level import here would make the cycle's success depend on
    # statement order inside ``__init__.py``.  A function-local import makes the
    # ordering irrelevant, because by the time anyone calls this the package is
    # fully initialised.
    from amplifier_agent_lib.persistence import amplifier_agent_home

    return amplifier_agent_home() / FOUNDATION_SUBDIR


def module_cache_root() -> Path:
    """Return the directory holding foundation's git clones of modules.

    Mirrors foundation's own layout (``<home>/cache``) as resolved by
    ``SimpleSourceResolver`` and ``ModuleActivator``.  Unlike the pre-decoupling
    situation this path is owned by amplifier-agent, which is what makes it safe
    for :mod:`amplifier_agent_lib.post_install` to delete entries from it.
    """
    return foundation_home() / "cache"


def legacy_module_clone_root() -> Path:
    """Return where module clones lived before this application owned them.

    ``~/.amplifier/cache`` -- foundation's default when ``AMPLIFIER_HOME`` is
    unset, and therefore where every amplifier-agent module clone was written
    prior to the bind in :func:`bind`.

    Deliberately ``Path.home()`` rather than :func:`amplifier_agent_home`: this
    reproduces what foundation's own fallback would have computed
    (``paths/resolution.py:152``), not anything this application configures.
    Used only for *reporting* and for the explicitly opt-in cleanup in
    ``amplifier-agent cache clear --legacy``.
    """
    return Path.home() / ".amplifier" / "cache"


def find_legacy_module_clones() -> list[Path]:
    """Return module clones stranded in amplifier-app-cli's tree, if any.

    After the relocation these are no longer read or written by amplifier-agent:
    the new clone root starts empty, so the first prepare after upgrading clones
    everything afresh and these directories simply stop being consulted.

    They are **not** deleted automatically, and that is the deliberate departure
    from PR #141, which removed them unconditionally.  The reason is ownership:
    the same directory is foundation's default for *every* Amplifier
    application on the machine, and the clone key is
    ``sha256(git_url@ref)[:16]`` -- not namespaced per app.  If amplifier-app-cli
    is installed, these are its live clones, not amplifier-agent's leftovers,
    and there is no way to tell the two cases apart from here.  Deleting them
    would be the same cross-application reach this decoupling exists to end,
    and would additionally risk removing a clone out from under a running
    app-cli session.

    #141's underlying concern -- do not silently leave the user on stale or
    wasted state -- is preserved by reporting them in ``doctor`` and offering
    ``cache clear --legacy``, so the choice is the user's rather than ours.

    Returns:
        Sorted list of ``amplifier-module-*`` directories, empty when the
        legacy root does not exist.
    """
    root = legacy_module_clone_root()
    if not root.is_dir():
        return []
    try:
        return sorted(
            child for child in root.iterdir() if child.is_dir() and child.name.startswith(LEGACY_MODULE_CLONE_PREFIX)
        )
    except OSError:
        # An unreadable legacy root is not this application's problem to
        # escalate -- it is reporting on a directory it no longer uses.
        return []


def _bind_context_intelligence() -> None:
    """Point the context-intelligence hook's *reader* root at our tree.

    ``hook-context-intelligence`` has a split root.  Its writer honours the
    ``base_path`` set in ``bundle.md`` (already amplifier-agent's own
    ``state/workspaces``), but its readers -- the discover, recipe and
    navigation skills -- resolve the root *only* from
    ``AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH``, falling back to
    ``~/.amplifier/projects``.

    Unset, that split is silent and wrong: captures are written into
    amplifier-agent's tree and then looked for in amplifier-app-cli's, so every
    read comes back empty.  The hook itself detects the mismatch and warns at
    runtime::

        context-intelligence: writer base_path (/root/.amplifier-agent/state/workspaces)
        and reader root (/root/.amplifier/projects) disagree -- ... captures written
        under /root/.amplifier-agent/state/workspaces will be invisible to them.

    Set only when absent, which is a deliberate departure from the
    unconditional ``AMPLIFIER_HOME`` bind above.  The two cases are not alike:
    inheriting app-cli's ``AMPLIFIER_HOME`` re-creates the shared-cache bug this
    module exists to remove, whereas a user who has explicitly exported this
    variable is making a considered choice to pool observability data across
    applications.  That is a legitimate thing to want, and it is their data.
    """
    if not os.environ.get(_CONTEXT_INTELLIGENCE_ENV):
        from amplifier_agent_lib.persistence import state_root

        os.environ[_CONTEXT_INTELLIGENCE_ENV] = str(state_root() / "workspaces")


def bind() -> Path:
    """Bind third-party storage roots into amplifier-agent's own tree.

    Points ``AMPLIFIER_HOME`` at :func:`foundation_home` and the
    context-intelligence reader root at this application's workspaces
    directory, then returns the foundation home.

    Idempotent, and safe to call from multiple entry points.  Must run before
    ``amplifier_foundation`` is imported -- see the module docstring.
    """
    home = foundation_home()
    os.environ[_FOUNDATION_ENV] = str(home)
    _bind_context_intelligence()
    return home
