"""Bundle loader — cold path for turning bundle.md into a PreparedBundle.

This module is the single entry point that loads and prepares the vendored
bundle.md (or an override path for dev/testing).  It does NOT cache; caching
lives in bundle/cache.py.

Per the D4 design decision the vendored bundle is *sealed*: production callers
always pass ``override_path=None`` so they get the vendored copy.  The
``override_path`` parameter exists exclusively for dev/testing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle import BUNDLE_MD

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle


async def load_and_prepare_bundle(
    override_path: Path | None = None,
    install_deps: bool = True,
) -> PreparedBundle:
    """Load and prepare the vendored bundle.md (or an override) via amplifier-foundation.

    This is the cold path — it always resolves and prepares the bundle from
    scratch.  Caching is the caller's responsibility (see bundle/cache.py).

    Args:
        override_path: If provided, load this path instead of the vendored
            ``BUNDLE_MD``.  For dev/testing only; production callers must leave
            this as ``None`` so the sealed vendored bundle is used.
        install_deps: Whether to install Python dependencies for each module
            declared in the bundle.  Pass ``False`` in unit tests to skip
            network access and speed up the test suite.

    Returns:
        A :class:`~amplifier_foundation.bundle._prepared.PreparedBundle`
        ready for session creation.

    Raises:
        FileNotFoundError: If the resolved target path does not exist on disk.
    """
    from amplifier_foundation import load_bundle

    target: Path = override_path if override_path is not None else BUNDLE_MD

    if not target.exists():
        raise FileNotFoundError(f"Bundle file not found: {target}")

    bundle = await load_bundle(f"file://{target}")
    prepared = await bundle.prepare(install_deps=install_deps)
    return prepared
