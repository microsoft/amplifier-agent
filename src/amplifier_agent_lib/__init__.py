"""amplifier_agent_lib — mode-agnostic Amplifier agent engine library.

This package is transport-free: it never reads from stdin or writes to stdout
directly.  All I/O flows through ProtocolPoints injected at Engine.boot().

See docs/designs/aaa-v2-design-checkpoint.md §5 for the naming rationale and
the "Critical invariant" that this separation enables (two invocation modes,
one engine implementation).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Source of truth: the ``[project].version`` field in ``pyproject.toml``,
    # surfaced via the installed distribution's metadata. Resolving at import
    # time keeps the engine ``__version__`` aligned with the packaging version
    # in editable, wheel, and uv-tool installs alike.
    __version__ = _pkg_version("amplifier-agent")
except PackageNotFoundError:  # pragma: no cover - source tree w/o dist-info
    # Fallback for the rare case where the package is on PYTHONPATH but not
    # installed (e.g. an in-tree script run before ``uv sync``). Keep this
    # string in sync with ``pyproject.toml`` if it ever has to fire.
    __version__ = "0.3.0"

# Bind amplifier_foundation's storage root into this application's own tree
# BEFORE anything imports amplifier_foundation.
#
# Placement is deliberate on two axes:
#
#   * After ``__version__`` is assigned, because ``foundation_home`` reaches
#     ``persistence``, which imports ``__version__`` back out of this module.
#     (``foundation_home`` defers that import into the function body, so this is
#     belt-and-braces rather than the sole defence.)
#
#   * At package-import time rather than inside a CLI entry point, because
#     ``amplifier_foundation.session.finder`` computes a ``~/.amplifier``-derived
#     module-level constant the moment it is imported.  Binding lazily would
#     leave that constant pointing into amplifier-app-cli's tree.
#
# See ``foundation_home`` for the full rationale.
from amplifier_agent_lib.foundation_home import bind as _bind_foundation_home

_bind_foundation_home()

__all__ = ["__version__"]
