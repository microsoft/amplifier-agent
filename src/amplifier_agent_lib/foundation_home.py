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
    "bind",
    "foundation_home",
    "module_cache_root",
]

#: Environment variable foundation itself consults.  Set by :func:`bind`.
_FOUNDATION_ENV = "AMPLIFIER_HOME"

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


def bind() -> Path:
    """Point ``AMPLIFIER_HOME`` at :func:`foundation_home` and return it.

    Idempotent, and safe to call from multiple entry points.  Must run before
    ``amplifier_foundation`` is imported -- see the module docstring.
    """
    home = foundation_home()
    os.environ[_FOUNDATION_ENV] = str(home)
    return home
